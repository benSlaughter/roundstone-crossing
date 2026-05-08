"""Tests for HistoryLogger (src/history.py)."""

import sqlite3
from datetime import datetime, timezone, timedelta

import pytest
from freezegun import freeze_time

from src.models import CrossingState, CrossingStatus, TrackedTrain, Direction, TrainPhase


# ---------------------------------------------------------------------------
# 1. DB initialisation
# ---------------------------------------------------------------------------

class TestDBInit:
    def test_tables_exist(self, history_db):
        db = sqlite3.connect(str(history_db.db_path))
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        db.close()
        for t in ("state_intervals", "train_passages", "raw_events", "train_events", "sf_events"):
            assert t in tables

    def test_indexes_exist(self, history_db):
        db = sqlite3.connect(str(history_db.db_path))
        indexes = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        db.close()
        expected = {
            "idx_intervals_started",
            "idx_passages_created",
            "idx_events_timestamp",
            "idx_train_events_ts",
            "idx_train_events_hc",
            "idx_sf_events_ts",
            "idx_sf_events_addr",
        }
        assert expected.issubset(indexes)


# ---------------------------------------------------------------------------
# 2. log_state_change
# ---------------------------------------------------------------------------

class TestLogStateChange:
    @freeze_time("2025-06-15 10:00:00", tz_offset=0)
    def test_first_call_creates_interval(self, history_db):
        status = CrossingStatus(state=CrossingState.OPEN, confidence=0.9)
        history_db.log_state_change(status)

        rows = history_db.get_intervals()
        assert len(rows) == 1
        assert rows[0]["state"] == "open"
        assert rows[0]["confidence"] == 0.9
        assert rows[0]["ended_at"] is None

    def test_different_state_closes_previous_and_opens_new(self, history_db):
        with freeze_time("2025-06-15 10:00:00", tz_offset=0):
            history_db.log_state_change(
                CrossingStatus(state=CrossingState.OPEN, confidence=0.9)
            )
        with freeze_time("2025-06-15 10:05:00", tz_offset=0):
            history_db.log_state_change(
                CrossingStatus(state=CrossingState.CLOSED_INFERRED, confidence=0.8)
            )

        rows = history_db.get_intervals()
        assert len(rows) == 2
        # DESC order — newest first
        assert rows[0]["state"] == "closed_inferred"
        assert rows[0]["ended_at"] is None
        # previous interval should be closed
        assert rows[1]["state"] == "open"
        assert rows[1]["ended_at"] is not None
        assert rows[1]["duration_secs"] is not None

    @freeze_time("2025-06-15 10:00:00", tz_offset=0)
    def test_same_state_does_not_create_new_interval(self, history_db):
        status = CrossingStatus(state=CrossingState.OPEN, confidence=0.9)
        history_db.log_state_change(status)
        history_db.log_state_change(status)

        rows = history_db.get_intervals()
        assert len(rows) == 1

    @freeze_time("2025-06-15 10:00:00", tz_offset=0)
    def test_reason_persisted_in_interval(self, history_db):
        """The reason field on CrossingStatus is stored in state_intervals."""
        status = CrossingStatus(
            state=CrossingState.CLOSING_PREDICTED, confidence=0.7,
            reason="route SET, no train in zone yet (R35,RA007) — early warning",
        )
        history_db.log_state_change(status)

        rows = history_db.get_intervals()
        assert len(rows) == 1
        assert rows[0]["reason"] == \
            "route SET, no train in zone yet (R35,RA007) — early warning"

    @freeze_time("2025-06-15 10:00:00", tz_offset=0)
    def test_each_transition_records_its_own_reason(self, history_db):
        """Different state transitions should each get their own reason recorded."""
        with freeze_time("2025-06-15 10:00:00", tz_offset=0):
            history_db.log_state_change(CrossingStatus(
                state=CrossingState.OPEN, confidence=0.8,
                reason="no trains in zone, no routes set",
            ))
        with freeze_time("2025-06-15 10:01:00", tz_offset=0):
            history_db.log_state_change(CrossingStatus(
                state=CrossingState.CLOSING_PREDICTED, confidence=0.7,
                reason="route SET, no train in zone yet (R35) — early warning",
            ))
        with freeze_time("2025-06-15 10:05:00", tz_offset=0):
            history_db.log_state_change(CrossingStatus(
                state=CrossingState.CLOSED_INFERRED, confidence=0.95,
                reason="train at crossing: 1A23 + routes (R35)",
            ))

        rows = history_db.get_intervals()
        assert len(rows) == 3
        # DESC order — newest first
        assert rows[0]["reason"].startswith("train at crossing")
        assert rows[1]["reason"].startswith("route SET")
        assert rows[2]["reason"].startswith("no trains")

    def test_reason_can_be_null_for_back_compat(self, history_db):
        """CrossingStatus without a reason (e.g. legacy callers) should still log fine."""
        status = CrossingStatus(state=CrossingState.OPEN, confidence=0.9)
        # reason defaults to None
        assert status.reason is None
        history_db.log_state_change(status)

        rows = history_db.get_intervals()
        assert len(rows) == 1
        assert rows[0]["reason"] is None


# ---------------------------------------------------------------------------
# 2b. Restart-survival: orphaned-interval cleanup at startup
# ---------------------------------------------------------------------------

class TestStartupOrphanCleanup:
    """When the previous logger lifecycle left state_intervals rows with
    `ended_at = NULL` (e.g. process killed before next state change), a
    fresh HistoryLogger should close those rows at __init__ time. Without
    this, restarts accumulate overlapping intervals that confuse downstream
    analysis (the metric script's loader had to compensate for this — now
    we fix it at the source).
    """

    def test_init_closes_unclosed_interval_from_prior_lifecycle(self, tmp_path):
        from src.history import HistoryLogger
        db_path = tmp_path / "test.db"

        # Lifecycle 1: open an interval, then "die" without closing
        with freeze_time("2025-06-15 10:00:00", tz_offset=0):
            h1 = HistoryLogger(db_path=db_path)
            h1.log_state_change(CrossingStatus(state=CrossingState.OPEN, confidence=0.9))
            del h1  # simulate process death (no clean shutdown)

        # Lifecycle 2: fresh logger — should close the orphaned interval
        with freeze_time("2025-06-15 10:05:00", tz_offset=0):
            h2 = HistoryLogger(db_path=db_path)

        rows = h2.get_intervals()
        assert len(rows) == 1
        # Now closed
        assert rows[0]["ended_at"] is not None
        assert rows[0]["duration_secs"] == pytest.approx(300, abs=2)  # 5 min gap

    def test_init_closes_multiple_orphans(self, tmp_path):
        """If somehow multiple unclosed rows exist (older bug, manual import),
        all should be closed at startup."""
        import sqlite3
        from src.history import HistoryLogger
        db_path = tmp_path / "test.db"

        # Pre-seed multiple unclosed intervals (simulating accumulated cruft)
        HistoryLogger(db_path=db_path)  # ensure schema exists
        db = sqlite3.connect(str(db_path))
        for state, ts in [
            ("open",            "2025-06-15T10:00:00+00:00"),
            ("closed_inferred", "2025-06-15T10:05:00+00:00"),
            ("open",            "2025-06-15T10:10:00+00:00"),
        ]:
            db.execute(
                "INSERT INTO state_intervals (state, started_at, ended_at) VALUES (?, ?, NULL)",
                (state, ts),
            )
        db.commit()
        db.close()

        # Reopen — should close all three
        with freeze_time("2025-06-15 10:15:00", tz_offset=0):
            HistoryLogger(db_path=db_path)

        db = sqlite3.connect(str(db_path))
        unclosed = db.execute("SELECT COUNT(*) FROM state_intervals WHERE ended_at IS NULL").fetchone()[0]
        db.close()
        assert unclosed == 0

    def test_init_with_no_orphans_is_noop(self, tmp_path):
        """Init on a clean DB doesn't error and doesn't touch any rows."""
        from src.history import HistoryLogger
        db_path = tmp_path / "test.db"
        HistoryLogger(db_path=db_path)
        # No exceptions; trivial DB exists with no state_intervals rows
        h2 = HistoryLogger(db_path=db_path)
        assert h2.get_intervals() == []

    def test_subsequent_state_change_after_restart_creates_new_interval(self, tmp_path):
        """After startup-cleanup closes the prior interval, the next
        log_state_change should INSERT a new row (not reopen the old one)
        — this preserves the gap visibility in history."""
        from src.history import HistoryLogger
        db_path = tmp_path / "test.db"

        # Lifecycle 1
        with freeze_time("2025-06-15 10:00:00", tz_offset=0):
            h1 = HistoryLogger(db_path=db_path)
            h1.log_state_change(CrossingStatus(state=CrossingState.OPEN, confidence=0.9))
            del h1

        # Lifecycle 2: same state as before. Should still create a new interval
        # (because we honestly don't know what happened during the gap).
        with freeze_time("2025-06-15 10:05:00", tz_offset=0):
            h2 = HistoryLogger(db_path=db_path)
            h2.log_state_change(CrossingStatus(state=CrossingState.OPEN, confidence=0.9))

        rows = h2.get_intervals()
        assert len(rows) == 2  # NOT 1 — the gap is real
        # Newest first (DESC)
        assert rows[0]["ended_at"] is None  # current open interval
        assert rows[1]["ended_at"] is not None  # closed at startup


# ---------------------------------------------------------------------------
# 2c. Thread-safety: concurrent log_state_change calls
# ---------------------------------------------------------------------------

class TestThreadSafety:
    """HistoryLogger is called from multiple threads (main loop + feed
    listener + API handlers via route_monitor). The shared Python state
    (_current_interval_id, _current_state) must be lock-protected.
    """

    def test_concurrent_same_state_does_not_create_duplicates(self, tmp_path):
        """Many threads calling log_state_change with the same state
        concurrently should still result in exactly one interval."""
        import threading
        from src.history import HistoryLogger
        db_path = tmp_path / "test.db"
        h = HistoryLogger(db_path=db_path)
        status = CrossingStatus(state=CrossingState.OPEN, confidence=0.9)

        barrier = threading.Barrier(20)

        def worker():
            barrier.wait()  # all threads start at the same instant
            for _ in range(50):
                h.log_state_change(status)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()

        rows = h.get_intervals()
        # Exactly one interval — 1000 calls all collapsed
        assert len(rows) == 1
        assert rows[0]["state"] == "open"

    def test_concurrent_alternating_states_consistent(self, tmp_path):
        """Threads alternating between two states — final _current_state
        must match the last successfully logged interval."""
        import threading
        from src.history import HistoryLogger
        db_path = tmp_path / "test.db"
        h = HistoryLogger(db_path=db_path)

        states = [
            CrossingStatus(state=CrossingState.OPEN, confidence=0.9),
            CrossingStatus(state=CrossingState.CLOSED_INFERRED, confidence=0.9),
        ]

        def worker(idx):
            for i in range(50):
                h.log_state_change(states[(idx + i) % 2])

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()

        # Logger's tracked state matches the most-recently-inserted row.
        rows = h.get_intervals(limit=1)  # newest first
        assert rows[0]["state"] == h._current_state.value

        # No row has ended_at < started_at (i.e. duration negative)
        all_rows = h.get_intervals(limit=1000)
        for r in all_rows[1:]:  # all except the open one
            assert r["ended_at"] is not None
            assert r["duration_secs"] >= 0


# ---------------------------------------------------------------------------
# 3. log_train_passage
# ---------------------------------------------------------------------------

class TestLogTrainPassage:
    @freeze_time("2025-06-15 10:30:00", tz_offset=0)
    def test_inserts_correct_fields(self, history_db):
        train = TrackedTrain(
            headcode="1A23",
            train_id="TRAIN-001",
            direction=Direction.UP,
            phase=TrainPhase.CLEARED,
            confidence=0.85,
            predicted_at_crossing=datetime(2025, 6, 15, 10, 28, 0, tzinfo=timezone.utc),
        )
        history_db.log_train_passage(train)

        rows = history_db.get_passages()
        assert len(rows) == 1
        row = rows[0]
        assert row["headcode"] == "1A23"
        assert row["train_id"] == "TRAIN-001"
        assert row["direction"] == "up"
        assert row["confidence"] == 0.85
        assert row["observed_at_crossing"] is not None  # predicted time stored here


# ---------------------------------------------------------------------------
# 4. log_train_event
# ---------------------------------------------------------------------------

class TestLogTrainEvent:
    @freeze_time("2025-06-15 11:00:00", tz_offset=0)
    def test_inserts_berth_step(self, history_db):
        history_db.log_train_event(
            headcode="2B45",
            event="berth_step",
            from_berth="0033",
            to_berth="0035",
            phase="approaching",
            direction="down",
        )

        rows = history_db.get_train_events(headcode="2B45")
        assert len(rows) == 1
        row = rows[0]
        assert row["headcode"] == "2B45"
        assert row["event"] == "berth_step"
        assert row["from_berth"] == "0033"
        assert row["to_berth"] == "0035"
        assert row["phase"] == "approaching"
        assert row["direction"] == "down"


# ---------------------------------------------------------------------------
# 5. log_raw_event
# ---------------------------------------------------------------------------

class TestLogRawEvent:
    @freeze_time("2025-06-15 12:00:00", tz_offset=0)
    def test_inserts_raw_event(self, history_db):
        history_db.log_raw_event("TD", "stomp", '{"msg": "hello"}')

        db = sqlite3.connect(str(history_db.db_path))
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute("SELECT * FROM raw_events").fetchall()]
        db.close()

        assert len(rows) == 1
        assert rows[0]["event_type"] == "TD"
        assert rows[0]["source"] == "stomp"
        assert rows[0]["data"] == '{"msg": "hello"}'
        assert rows[0]["timestamp"] is not None


# ---------------------------------------------------------------------------
# 6. get_intervals
# ---------------------------------------------------------------------------

class TestGetIntervals:
    def _seed_intervals(self, history_db):
        """Create 3 intervals at different times."""
        with freeze_time("2025-06-15 08:00:00", tz_offset=0):
            history_db.log_state_change(
                CrossingStatus(state=CrossingState.OPEN, confidence=0.9)
            )
        with freeze_time("2025-06-15 09:00:00", tz_offset=0):
            history_db.log_state_change(
                CrossingStatus(state=CrossingState.CLOSED_INFERRED, confidence=0.8)
            )
        with freeze_time("2025-06-15 10:00:00", tz_offset=0):
            history_db.log_state_change(
                CrossingStatus(state=CrossingState.OPEN, confidence=0.95)
            )

    def test_returns_records(self, history_db):
        self._seed_intervals(history_db)
        rows = history_db.get_intervals()
        assert len(rows) == 3

    def test_desc_order(self, history_db):
        self._seed_intervals(history_db)
        rows = history_db.get_intervals()
        assert rows[0]["state"] == "open"          # 10:00
        assert rows[1]["state"] == "closed_inferred"  # 09:00
        assert rows[2]["state"] == "open"           # 08:00

    def test_respects_limit(self, history_db):
        self._seed_intervals(history_db)
        rows = history_db.get_intervals(limit=2)
        assert len(rows) == 2

    def test_respects_since_filter(self, history_db):
        self._seed_intervals(history_db)
        rows = history_db.get_intervals(since="2025-06-15T09:30:00+00:00")
        assert len(rows) == 1
        assert rows[0]["state"] == "open"


# ---------------------------------------------------------------------------
# 7. get_passages
# ---------------------------------------------------------------------------

class TestGetPassages:
    def _seed_passages(self, history_db):
        for i, hc in enumerate(["1A01", "2B02", "3C03"]):
            with freeze_time(f"2025-06-15 10:{i:02d}:00", tz_offset=0):
                train = TrackedTrain(
                    headcode=hc, direction=Direction.UP, phase=TrainPhase.CLEARED,
                    confidence=0.7,
                )
                history_db.log_train_passage(train)

    def test_returns_records(self, history_db):
        self._seed_passages(history_db)
        rows = history_db.get_passages()
        assert len(rows) == 3

    def test_respects_limit(self, history_db):
        self._seed_passages(history_db)
        rows = history_db.get_passages(limit=1)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# 8. get_train_events
# ---------------------------------------------------------------------------

class TestGetTrainEvents:
    def _seed_events(self, history_db):
        with freeze_time("2025-06-15 10:00:00", tz_offset=0):
            history_db.log_train_event("1A23", "berth_step", "0042", "0040")
        with freeze_time("2025-06-15 10:01:00", tz_offset=0):
            history_db.log_train_event("1A23", "berth_step", "0040", "0038")
        with freeze_time("2025-06-15 10:02:00", tz_offset=0):
            history_db.log_train_event("2B45", "berth_step", "0033", "0035")

    def test_filter_by_headcode(self, history_db):
        self._seed_events(history_db)
        rows = history_db.get_train_events(headcode="1A23")
        assert len(rows) == 2
        assert all(r["headcode"] == "1A23" for r in rows)

    def test_filter_by_since(self, history_db):
        self._seed_events(history_db)
        rows = history_db.get_train_events(since="2025-06-15T10:01:30+00:00")
        assert len(rows) == 1
        assert rows[0]["headcode"] == "2B45"


# ---------------------------------------------------------------------------
# 9. get_stats
# ---------------------------------------------------------------------------

class TestGetStats:
    def test_returns_correct_counts(self, history_db):
        with freeze_time("2025-06-15 10:00:00", tz_offset=0):
            history_db.log_state_change(
                CrossingStatus(state=CrossingState.OPEN, confidence=0.9)
            )
            train = TrackedTrain(
                headcode="1A23", direction=Direction.UP,
                phase=TrainPhase.CLEARED, confidence=0.8,
            )
            history_db.log_train_passage(train)

        stats = history_db.get_stats()
        assert stats["total_intervals"] == 1
        assert stats["total_passages"] == 1

    def test_avg_closure_duration(self, history_db):
        # Create a closed_inferred interval with known duration
        with freeze_time("2025-06-15 10:00:00", tz_offset=0):
            history_db.log_state_change(
                CrossingStatus(state=CrossingState.CLOSED_INFERRED, confidence=0.8)
            )
        with freeze_time("2025-06-15 10:05:00", tz_offset=0):
            history_db.log_state_change(
                CrossingStatus(state=CrossingState.OPEN, confidence=0.95)
            )

        stats = history_db.get_stats()
        assert stats["avg_closure_duration_secs"] is not None
        assert abs(stats["avg_closure_duration_secs"] - 300.0) < 1.0

    def test_avg_closure_duration_none_when_no_closed(self, history_db):
        with freeze_time("2025-06-15 10:00:00", tz_offset=0):
            history_db.log_state_change(
                CrossingStatus(state=CrossingState.OPEN, confidence=0.9)
            )
        stats = history_db.get_stats()
        assert stats["avg_closure_duration_secs"] is None


# ---------------------------------------------------------------------------
# 10. SF events (S-Class signalling)
# ---------------------------------------------------------------------------

class TestSfEvents:
    @freeze_time("2025-06-15 10:00:00", tz_offset=0)
    def test_record_sf_event_stores_correctly(self, history_db):
        history_db.record_sf_event("LA", "16", "43")
        events = history_db.get_sf_events()
        assert len(events) == 1
        ev = events[0]
        assert ev["area_id"] == "LA"
        assert ev["address"] == "16"
        assert ev["data_hex"] == "43"
        assert ev["data_bin"] == "01000011"

    @freeze_time("2025-06-15 10:00:00", tz_offset=0)
    def test_record_sf_event_hex_to_bin_conversion(self, history_db):
        cases = [("00", "00000000"), ("FF", "11111111"), ("A5", "10100101")]
        for hex_val, expected_bin in cases:
            history_db.record_sf_event("LA", "10", hex_val)
        events = history_db.get_sf_events()
        # DESC order, so newest first
        bins = [ev["data_bin"] for ev in reversed(events)]
        assert bins == ["00000000", "11111111", "10100101"]

    @freeze_time("2025-06-15 10:00:00", tz_offset=0)
    def test_get_sf_events_filter_by_address(self, history_db):
        history_db.record_sf_event("LA", "16", "43")
        history_db.record_sf_event("LA", "2F", "FF")
        history_db.record_sf_event("LA", "16", "44")

        events = history_db.get_sf_events(address="16")
        assert len(events) == 2
        assert all(ev["address"] == "16" for ev in events)

    def test_get_sf_events_filter_by_since(self, history_db):
        with freeze_time("2025-06-15 08:00:00", tz_offset=0):
            history_db.record_sf_event("LA", "16", "01")
        with freeze_time("2025-06-15 10:00:00", tz_offset=0):
            history_db.record_sf_event("LA", "16", "02")
        with freeze_time("2025-06-15 12:00:00", tz_offset=0):
            history_db.record_sf_event("LA", "16", "03")

        events = history_db.get_sf_events(since="2025-06-15T09:00:00+00:00")
        assert len(events) == 2
        hex_values = {ev["data_hex"] for ev in events}
        assert hex_values == {"02", "03"}

    @freeze_time("2025-06-15 10:00:00", tz_offset=0)
    def test_get_sf_events_respects_limit(self, history_db):
        for i in range(5):
            history_db.record_sf_event("LA", "16", f"{i:02X}")

        events = history_db.get_sf_events(limit=2)
        assert len(events) == 2

    def test_get_sf_summary(self, history_db):
        with freeze_time("2025-06-15 08:00:00", tz_offset=0):
            history_db.record_sf_event("LA", "16", "01")
        with freeze_time("2025-06-15 09:00:00", tz_offset=0):
            history_db.record_sf_event("LA", "16", "02")
        with freeze_time("2025-06-15 10:00:00", tz_offset=0):
            history_db.record_sf_event("LA", "16", "03")
        with freeze_time("2025-06-15 09:30:00", tz_offset=0):
            history_db.record_sf_event("LA", "2F", "FF")

        summary = history_db.get_sf_summary()
        assert len(summary) == 2
        # Ordered by change_count DESC
        assert summary[0]["address"] == "16"
        assert summary[0]["change_count"] == 3
        assert summary[0]["first_seen"] is not None
        assert summary[0]["last_seen"] is not None
        assert summary[1]["address"] == "2F"
        assert summary[1]["change_count"] == 1
