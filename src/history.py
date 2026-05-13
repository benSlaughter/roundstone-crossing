"""
Historical logger — stores every crossing state change and train passage in SQLite.
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import CrossingState, CrossingStatus, TrackedTrain

logger = logging.getLogger("crossing.history")

DB_PATH = Path(os.environ.get("CROSSING_DB_PATH", str(Path(__file__).parent.parent / "crossing.db")))


class HistoryLogger:
    """Logs crossing state intervals and train passages to SQLite.

    Thread-safety
    -------------
    SQLite-level concurrency is handled per-connection (each method opens
    its own connection with `busy_timeout` set). The Python-level state
    (`_current_interval_id`, `_current_state`) is protected by `_lock` —
    necessary because `log_state_change()` is called from at least three
    threads in production (main loop, NROD feed listener, FastAPI handlers
    via the route_monitor's SF callbacks).
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        # Lock guards _current_interval_id and _current_state. SQLite handles
        # its own connection-level concurrency separately.
        self._lock = threading.Lock()
        self._init_db()
        self._current_interval_id: int | None = None
        self._current_state: CrossingState | None = None

        # Close any state intervals left unclosed by a previous logger
        # lifecycle (e.g. app restart, container redeploy). Without this,
        # restarts accumulate "open-ended" rows that confuse downstream
        # analysis (every restart leaves behind a row with ended_at=NULL,
        # and a fresh logger would happily insert a new interval over the
        # top of it). We can't reconstruct what the predictor was inferring
        # during the gap, so we close at startup time and let the next
        # log_state_change() open a fresh interval naturally — this records
        # the gap honestly rather than pretending continuity.
        self._close_orphaned_intervals_at_startup()
        self._close_orphaned_route_intervals_at_startup()

    def _connect(self) -> sqlite3.Connection:
        """Create a DB connection with consistent settings."""
        db = sqlite3.connect(str(self.db_path))
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _init_db(self):
        db = self._connect()

        db.execute("""
            CREATE TABLE IF NOT EXISTS state_intervals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state TEXT NOT NULL,
                confidence REAL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_secs REAL,
                active_train_count INTEGER DEFAULT 0,
                notes TEXT,
                reason TEXT
            )
        """)

        # Idempotent migration for pre-existing databases that lack `reason`.
        existing_cols = {r[1] for r in db.execute(
            "PRAGMA table_info(state_intervals)").fetchall()}
        if "reason" not in existing_cols:
            db.execute("ALTER TABLE state_intervals ADD COLUMN reason TEXT")
            logger.info("Migrated state_intervals: added 'reason' column")

        db.execute("""
            CREATE TABLE IF NOT EXISTS train_passages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                headcode TEXT NOT NULL,
                train_id TEXT,
                direction TEXT,
                first_seen TEXT,
                predicted_close TEXT,
                predicted_open TEXT,
                observed_at_crossing TEXT,    -- actually stores predicted_at_crossing time
                observed_clear TEXT,
                confidence REAL,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS raw_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                data TEXT,
                timestamp TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS train_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                headcode TEXT NOT NULL,
                direction TEXT,
                event TEXT NOT NULL,
                from_berth TEXT,
                to_berth TEXT,
                phase TEXT,
                timestamp TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS sf_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                area_id TEXT NOT NULL,
                address TEXT NOT NULL,
                data_hex TEXT NOT NULL,
                data_bin TEXT NOT NULL
            )
        """)

        # Indexes for common queries
        db.execute("CREATE INDEX IF NOT EXISTS idx_intervals_started ON state_intervals(started_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_passages_created ON train_passages(created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON raw_events(timestamp)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_train_events_ts ON train_events(timestamp)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_train_events_hc ON train_events(headcode, timestamp)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sf_events_ts ON sf_events(timestamp)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sf_events_addr ON sf_events(address, timestamp)")

        db.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                user_agent TEXT,
                created_at TEXT NOT NULL
            )
        """)

        # Per-tick prediction snapshot. Logged on EVERY main-loop tick (~every
        # 2s), regardless of whether state changed. This is the ground-truth
        # comparison surface for downstream camera analysis: at any moment in
        # the past we want to know exactly what the system was reporting,
        # what it predicted next, and what it knew. Per-train detail lives
        # in `train_snapshots` (joined by timestamp); per-route SET/CLEAR
        # history lives in `route_intervals`.
        db.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                state TEXT NOT NULL,
                confidence REAL,
                predicted_change_at TEXT,
                predicted_next_state TEXT,
                active_train_count INTEGER DEFAULT 0,
                active_route_count INTEGER DEFAULT 0,
                feed_age_secs REAL,
                config_hash TEXT,
                reason TEXT
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_predictions_ts ON predictions(timestamp)")

        # Per-tick per-train snapshots. One row per active train per
        # main-loop tick. Lets downstream analysis ask "where was train X
        # at time T?" or "what trains were near the crossing at time T?"
        # without parsing JSON. The `tick_timestamp` column matches the
        # corresponding `predictions.timestamp` exactly so JOINs are easy.
        db.execute("""
            CREATE TABLE IF NOT EXISTS train_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tick_timestamp TEXT NOT NULL,
                headcode TEXT NOT NULL,
                train_id TEXT,
                direction TEXT,
                phase TEXT,
                last_berth TEXT,
                predicted_at_crossing TEXT,
                confidence REAL,
                first_seen TEXT
            )
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_train_snapshots_ts "
            "ON train_snapshots(tick_timestamp)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_train_snapshots_hc "
            "ON train_snapshots(headcode, tick_timestamp)"
        )

        # Resolved route SET/CLEAR intervals — derived from sf_events, but
        # easier to query because the bytes have already been decoded into
        # named routes. Critical distinction: `cleared_at` is only set on an
        # actual observed CLEAR (route bit transitioning 1→0). Disconnects
        # and process restarts close intervals via `end_reason` and
        # `observed_until`, NEVER by inventing a fake `cleared_at`.
        db.execute("""
            CREATE TABLE IF NOT EXISTS route_intervals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_name TEXT NOT NULL,
                set_at TEXT NOT NULL,
                cleared_at TEXT,
                observed_until TEXT,
                duration_secs REAL,
                end_reason TEXT,
                notes TEXT
            )
        """)
        # Partial unique index enforces "one open interval per route" at the
        # DB level, so any future bug that double-SETs a route fails fast
        # instead of silently corrupting history.
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_route_intervals_one_open "
            "ON route_intervals(route_name) WHERE end_reason IS NULL"
        )
        # Indexes targeting the dominant analytical query: "which routes
        # were SET (and observable) at time T?"
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_route_intervals_time "
            "ON route_intervals(set_at, observed_until)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_route_intervals_route_time "
            "ON route_intervals(route_name, set_at, observed_until)"
        )

        db.commit()
        db.close()
        logger.info(f"History database: {self.db_path}")

    def _close_orphaned_intervals_at_startup(self):
        """Close any state_intervals rows left with `ended_at` NULL.

        Each such row represents an interval that the previous logger
        instance opened but never closed (typically because the process was
        killed/restarted before the next state transition). Without this
        cleanup, downstream analysis sees overlapping intervals and the
        next `log_state_change()` would create a third row covering the
        same period.
        """
        now = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        try:
            cursor = db.execute(
                "UPDATE state_intervals SET "
                "ended_at = ?, "
                "duration_secs = (julianday(?) - julianday(started_at)) * 86400 "
                "WHERE ended_at IS NULL",
                (now, now),
            )
            n = cursor.rowcount
            db.commit()
            if n > 0:
                logger.info(
                    f"Closed {n} orphaned state interval(s) at startup "
                    "(prior process restart left them open-ended)"
                )
        finally:
            db.close()

    def _close_orphaned_route_intervals_at_startup(self):
        """Close any route_intervals rows left open by a previous process.

        These are routes that were SET when the prior process exited. We
        don't know whether the route actually cleared during the gap, so we
        mark them as `end_reason='startup_orphan'` and `observed_until=now`
        — preserving the uncertainty rather than inventing a `cleared_at`.
        """
        now = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        try:
            cursor = db.execute(
                "UPDATE route_intervals SET "
                "observed_until = ?, "
                "duration_secs = (julianday(?) - julianday(set_at)) * 86400, "
                "end_reason = 'startup_orphan' "
                "WHERE end_reason IS NULL",
                (now, now),
            )
            n = cursor.rowcount
            db.commit()
            if n > 0:
                logger.info(
                    f"Closed {n} orphaned route interval(s) at startup "
                    "(prior process exited while routes were SET)"
                )
        finally:
            db.close()

    def log_state_change(self, status: CrossingStatus):
        """Log a crossing state transition.

        On transition (state actually changes), opens a new state_intervals row
        with the current state's reason. The reason is captured at insert time
        only — re-asserting the same state on subsequent ticks does not update
        the stored reason (it always describes why the state was first entered).

        Thread-safe: wrapped in `_lock` because `_current_interval_id` and
        `_current_state` are read and written across threads (main loop +
        feed listener + API handlers via route_monitor). The timestamp is
        captured inside the lock so concurrent calls produce monotonically
        increasing intervals (no negative durations).
        """
        db = self._connect()
        try:
            with self._lock:
                now = datetime.now(timezone.utc).isoformat()

                # Close previous interval
                if self._current_interval_id and self._current_state != status.state:
                    db.execute(
                        "UPDATE state_intervals SET ended_at = ?, duration_secs = "
                        "(julianday(?) - julianday(started_at)) * 86400 WHERE id = ?",
                        (now, now, self._current_interval_id),
                    )

                # Open new interval
                if self._current_state != status.state:
                    cursor = db.execute(
                        "INSERT INTO state_intervals "
                        "(state, confidence, started_at, active_train_count, reason) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (status.state.value, status.confidence, now,
                         len(status.active_trains), status.reason),
                    )
                    self._current_interval_id = cursor.lastrowid
                    self._current_state = status.state
                    logger.debug(f"Logged state: {status.state.value} (interval #{self._current_interval_id}) — {status.reason}")

                db.commit()
        finally:
            db.close()

    def log_train_passage(self, train: TrackedTrain):
        """Log a complete train passage (when train clears the crossing).

        Note: 'observed_at_crossing' column stores the predicted crossing time
        (from train.predicted_at_crossing), not an actual observed timestamp.
        A future schema migration could rename this column.
        """
        now = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        db.execute(
            """INSERT INTO train_passages
               (headcode, train_id, direction, first_seen, observed_at_crossing,
                observed_clear, confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                train.headcode,
                train.train_id,
                train.direction.value if train.direction else None,
                train.first_seen.isoformat(),
                train.predicted_at_crossing.isoformat() if train.predicted_at_crossing else None,
                now,
                train.confidence,
                now,
            ),
        )
        db.commit()
        db.close()
        logger.debug(f"Logged passage: {train.headcode} ({train.direction})")

    def log_train_event(self, headcode: str, event: str, from_berth: str = None,
                        to_berth: str = None, phase: str = None, direction: str = None):
        """Log a berth step or phase change for a train."""
        now = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        db.execute(
            "INSERT INTO train_events (headcode, direction, event, from_berth, to_berth, phase, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (headcode, direction, event, from_berth, to_berth, phase, now),
        )
        db.commit()
        db.close()

    def log_raw_event(self, event_type: str, source: str, data: str):
        """Log a raw TD/TRUST message for debugging and analysis."""
        now = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        db.execute(
            "INSERT INTO raw_events (event_type, source, data, timestamp) VALUES (?, ?, ?, ?)",
            (event_type, source, data, now),
        )
        db.commit()
        db.close()

    # ── Predictions ──────────────────────────────────────────────────────

    def log_prediction(self, status: CrossingStatus, feed_age_secs: float | None = None,
                       active_routes: list | None = None, config_hash: str | None = None):
        """Log a per-tick prediction snapshot.

        Logged on every main-loop tick (~every 2s), not only on state change.
        Per-train detail is logged separately by `log_train_snapshots()`.

        Args:
            status: CrossingStatus from the inferrer
            feed_age_secs: Seconds since last NROD message, or None
            active_routes: List of active route names or RouteInfo objects.
                           Only used to record the count here; details live
                           in the `route_intervals` table.
            config_hash: Short hash of inference-relevant config for provenance
        """
        now = datetime.now(timezone.utc).isoformat()

        predicted_change_at = (status.predicted_change.isoformat()
                               if status.predicted_change else None)
        predicted_next = (status.predicted_next_state.value
                          if status.predicted_next_state else None)

        db = self._connect()
        try:
            db.execute(
                "INSERT INTO predictions "
                "(timestamp, state, confidence, predicted_change_at, "
                " predicted_next_state, active_train_count, active_route_count, "
                " feed_age_secs, config_hash, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now, status.state.value, status.confidence,
                    predicted_change_at, predicted_next,
                    len(status.active_trains or []),
                    len(active_routes or []),
                    feed_age_secs, config_hash, status.reason,
                ),
            )
            db.commit()
        finally:
            db.close()
        return now  # Return so callers can pass it to log_train_snapshots for exact alignment

    def log_train_snapshots(self, trains: list, tick_timestamp: str | None = None):
        """Log one row per active train at the current tick.

        Args:
            trains: List of TrackedTrain objects (the same list passed to inferrer)
            tick_timestamp: ISO timestamp to attach to every row. If None,
                            uses the current time. For exact alignment with
                            `predictions`, pass the value returned by
                            `log_prediction()`.
        """
        if not trains:
            return
        ts = tick_timestamp or datetime.now(timezone.utc).isoformat()
        rows = [
            (
                ts,
                t.headcode,
                t.train_id,
                t.direction.value if t.direction else None,
                t.phase.value if t.phase else None,
                t.last_berth,
                t.predicted_at_crossing.isoformat() if t.predicted_at_crossing else None,
                t.confidence,
                t.first_seen.isoformat() if t.first_seen else None,
            )
            for t in trains
        ]
        db = self._connect()
        try:
            db.executemany(
                "INSERT INTO train_snapshots "
                "(tick_timestamp, headcode, train_id, direction, phase, "
                " last_berth, predicted_at_crossing, confidence, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            db.commit()
        finally:
            db.close()

    # ── Route intervals ──────────────────────────────────────────────────

    def start_route_interval(self, route_name: str, set_at: datetime):
        """Record that a route went from CLEAR to SET (observed transition).

        If a row already exists for this route with end_reason IS NULL, the
        UNIQUE INDEX will reject the INSERT — log the conflict and skip,
        because emitting two SETs without a CLEAR in between is a bug
        upstream (RouteMonitor only emits on bit transition).
        """
        db = self._connect()
        try:
            db.execute(
                "INSERT INTO route_intervals (route_name, set_at) VALUES (?, ?)",
                (route_name, set_at.isoformat() if isinstance(set_at, datetime) else set_at),
            )
            db.commit()
        except sqlite3.IntegrityError:
            logger.warning(
                f"Route {route_name} already has an open interval — "
                f"refusing to insert a second SET. This indicates the route "
                f"monitor double-emitted a SET without an intervening CLEAR."
            )
        finally:
            db.close()

    def close_route_interval(self, route_name: str, end_at: datetime,
                             end_reason: str = "observed_clear"):
        """Close the currently-open interval for `route_name`.

        Args:
            route_name: Route to close
            end_at: Timestamp of the close event
            end_reason: One of:
                - 'observed_clear': route bit transitioned 1→0. `cleared_at` is set.
                - 'disconnect': feed dropped while route was SET. `cleared_at`
                  stays NULL (we did not observe a clear); `observed_until` is
                  set instead.
                - 'startup_orphan': handled by _close_orphaned_route_intervals_at_startup.
                - 'duplicate_set': supersedes a stale open interval (defensive).
        """
        end_iso = end_at.isoformat() if isinstance(end_at, datetime) else end_at
        cleared_at_value = end_iso if end_reason == "observed_clear" else None

        db = self._connect()
        try:
            cursor = db.execute(
                "UPDATE route_intervals SET "
                "cleared_at = ?, "
                "observed_until = ?, "
                "duration_secs = (julianday(?) - julianday(set_at)) * 86400, "
                "end_reason = ? "
                "WHERE id = ("
                "  SELECT id FROM route_intervals "
                "  WHERE route_name = ? AND end_reason IS NULL "
                "  ORDER BY id DESC LIMIT 1"
                ")",
                (cleared_at_value, end_iso, end_iso, end_reason, route_name),
            )
            if cursor.rowcount == 0:
                logger.debug(
                    f"close_route_interval({route_name}, {end_reason}): "
                    f"no open interval to close (already closed or never started)"
                )
            db.commit()
        finally:
            db.close()

    def close_all_open_route_intervals(self, end_at: datetime,
                                       end_reason: str = "disconnect") -> int:
        """Close every currently-open route interval (used on feed disconnect).

        Returns the number of intervals closed.
        """
        end_iso = end_at.isoformat() if isinstance(end_at, datetime) else end_at
        cleared_at_value = end_iso if end_reason == "observed_clear" else None

        db = self._connect()
        try:
            cursor = db.execute(
                "UPDATE route_intervals SET "
                "cleared_at = ?, "
                "observed_until = ?, "
                "duration_secs = (julianday(?) - julianday(set_at)) * 86400, "
                "end_reason = ? "
                "WHERE end_reason IS NULL",
                (cleared_at_value, end_iso, end_iso, end_reason),
            )
            n = cursor.rowcount
            db.commit()
            if n > 0:
                logger.info(f"Closed {n} open route interval(s) ({end_reason})")
            return n
        finally:
            db.close()

    def get_intervals(self, since: str = None, limit: int = 100) -> list[dict]:
        """Query historical state intervals."""
        db = self._connect()
        db.row_factory = sqlite3.Row
        if since:
            rows = db.execute(
                "SELECT * FROM state_intervals WHERE started_at >= ? ORDER BY started_at DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM state_intervals ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def get_passages(self, since: str = None, limit: int = 100) -> list[dict]:
        """Query historical train passages."""
        db = self._connect()
        db.row_factory = sqlite3.Row
        if since:
            rows = db.execute(
                "SELECT * FROM train_passages WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM train_passages ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def get_train_events(self, headcode: str = None, since: str = None, limit: int = 200) -> list[dict]:
        """Query train berth stepping events for calibration."""
        db = self._connect()
        db.row_factory = sqlite3.Row
        if headcode:
            rows = db.execute(
                "SELECT * FROM train_events WHERE headcode = ? ORDER BY timestamp ASC LIMIT ?",
                (headcode, limit),
            ).fetchall()
        elif since:
            rows = db.execute(
                "SELECT * FROM train_events WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM train_events ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """Get summary statistics."""
        db = self._connect()
        stats = {}
        row = db.execute("SELECT COUNT(*) FROM state_intervals").fetchone()
        stats["total_intervals"] = row[0]
        row = db.execute("SELECT COUNT(*) FROM train_passages").fetchone()
        stats["total_passages"] = row[0]
        row = db.execute(
            "SELECT AVG(duration_secs) FROM state_intervals WHERE state = 'closed_inferred' AND duration_secs IS NOT NULL"
        ).fetchone()
        stats["avg_closure_duration_secs"] = round(row[0], 1) if row[0] else None
        db.close()
        return stats

    def record_sf_event(self, area_id: str, address: str, data_hex: str):
        """Record an S-Class signalling state change."""
        # Convert hex to 8-bit binary string
        data_bin = format(int(data_hex, 16), '08b')
        now = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        db.execute(
            "INSERT INTO sf_events (timestamp, area_id, address, data_hex, data_bin) VALUES (?, ?, ?, ?, ?)",
            (now, area_id, address, data_hex, data_bin),
        )
        db.commit()
        db.close()
        logger.debug(f"SF event: area={area_id} addr={address} data=0x{data_hex} ({data_bin})")

    def get_sf_events(self, since: str = None, address: str = None, limit: int = 100) -> list[dict]:
        """Query S-Class signalling events."""
        db = self._connect()
        db.row_factory = sqlite3.Row
        conditions = []
        params = []
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if address:
            conditions.append("address = ?")
            params.append(address)
        where = " AND ".join(conditions)
        if where:
            where = "WHERE " + where
        params.append(limit)
        rows = db.execute(
            f"SELECT * FROM sf_events {where} ORDER BY timestamp DESC LIMIT ?", params
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def submit_feedback(self, message: str, user_agent: str = None) -> int:
        """Store a feedback submission. Returns the feedback ID."""
        now = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        cur = db.execute(
            "INSERT INTO feedback (message, user_agent, created_at) VALUES (?, ?, ?)",
            (message, user_agent, now),
        )
        feedback_id = cur.lastrowid
        db.commit()
        db.close()
        logger.info(f"Feedback #{feedback_id} submitted")
        return feedback_id

    def get_feedback(self, limit: int = 50) -> list[dict]:
        """Retrieve recent feedback submissions."""
        db = self._connect()
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT * FROM feedback ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def get_sf_summary(self) -> list[dict]:
        """Summary of S-Class addresses seen: distinct addresses, change counts, last seen."""
        db = self._connect()
        db.row_factory = sqlite3.Row
        rows = db.execute("""
            SELECT address, COUNT(*) as change_count,
                   MAX(timestamp) as last_seen,
                   MIN(timestamp) as first_seen
            FROM sf_events
            GROUP BY address
            ORDER BY change_count DESC
        """).fetchall()
        db.close()
        return [dict(r) for r in rows]
