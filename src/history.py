"""
Historical logger — stores every crossing state change and train passage in SQLite.
"""

import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

from .models import CrossingState, CrossingStatus, TrackedTrain

logger = logging.getLogger("crossing.history")

DB_PATH = Path(__file__).parent.parent / "crossing.db"


class HistoryLogger:
    """Logs crossing state intervals and train passages to SQLite."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()
        self._current_interval_id: int | None = None
        self._current_state: CrossingState | None = None

    def _init_db(self):
        db = sqlite3.connect(str(self.db_path))
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")

        db.execute("""
            CREATE TABLE IF NOT EXISTS state_intervals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state TEXT NOT NULL,
                confidence REAL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_secs REAL,
                active_train_count INTEGER DEFAULT 0,
                notes TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS train_passages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                headcode TEXT NOT NULL,
                train_id TEXT,
                direction TEXT,
                first_seen TEXT,
                predicted_close TEXT,
                predicted_open TEXT,
                observed_at_crossing TEXT,
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

        # Indexes for common queries
        db.execute("CREATE INDEX IF NOT EXISTS idx_intervals_started ON state_intervals(started_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_passages_created ON train_passages(created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON raw_events(timestamp)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_train_events_ts ON train_events(timestamp)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_train_events_hc ON train_events(headcode, timestamp)")

        db.commit()
        db.close()
        logger.info(f"📊 History database: {self.db_path}")

    def log_state_change(self, status: CrossingStatus):
        """Log a crossing state transition."""
        now = datetime.now(timezone.utc).isoformat()
        db = sqlite3.connect(str(self.db_path))

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
                "INSERT INTO state_intervals (state, confidence, started_at, active_train_count) VALUES (?, ?, ?, ?)",
                (status.state.value, status.confidence, now, len(status.active_trains)),
            )
            self._current_interval_id = cursor.lastrowid
            self._current_state = status.state
            logger.debug(f"📊 Logged state: {status.state.value} (interval #{self._current_interval_id})")

        db.commit()
        db.close()

    def log_train_passage(self, train: TrackedTrain):
        """Log a complete train passage (when train clears the crossing)."""
        now = datetime.now(timezone.utc).isoformat()
        db = sqlite3.connect(str(self.db_path))
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
        logger.debug(f"📊 Logged passage: {train.headcode} ({train.direction})")

    def log_train_event(self, headcode: str, event: str, from_berth: str = None,
                        to_berth: str = None, phase: str = None, direction: str = None):
        """Log a berth step or phase change for a train."""
        now = datetime.now(timezone.utc).isoformat()
        db = sqlite3.connect(str(self.db_path))
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
        db = sqlite3.connect(str(self.db_path))
        db.execute(
            "INSERT INTO raw_events (event_type, source, data, timestamp) VALUES (?, ?, ?, ?)",
            (event_type, source, data, now),
        )
        db.commit()
        db.close()

    def get_intervals(self, since: str = None, limit: int = 100) -> list[dict]:
        """Query historical state intervals."""
        db = sqlite3.connect(str(self.db_path))
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
        db = sqlite3.connect(str(self.db_path))
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
        db = sqlite3.connect(str(self.db_path))
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
        db = sqlite3.connect(str(self.db_path))
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
