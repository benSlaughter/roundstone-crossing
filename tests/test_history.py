"""Tests for HistoryLogger (src/history.py)."""

import sqlite3
from datetime import datetime, timezone, timedelta

import pytest
from freezegun import freeze_time

from src.models import CrossingState, CrossingStatus, TrackedTrain, Direction, TrainPhase


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


class TestLogPrediction:
    def test_creates_predictions_table(self, history_db):
        db = sqlite3.connect(str(history_db.db_path))
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        db.close()
        assert "predictions" in tables

    def test_logs_minimal_status(self, history_db):
        status = CrossingStatus()
        status.state = CrossingState.OPEN
        status.confidence = 0.8
        status.reason = "no trains"

        history_db.log_prediction(status)

        db = sqlite3.connect(str(history_db.db_path))
        rows = db.execute("SELECT state, confidence, reason FROM predictions").fetchall()
        db.close()
        assert len(rows) == 1
        assert rows[0] == ("open", 0.8, "no trains")

    def test_logs_full_snapshot_with_counts(self, history_db, make_train):
        train = make_train(headcode="1A23", phase=TrainPhase.AT_CROSSING,
                           direction=Direction.UP, last_berth="0036",
                           confidence=0.9)
        status = CrossingStatus()
        status.state = CrossingState.CLOSED_INFERRED
        status.confidence = 0.95
        status.active_trains = [train]
        status.predicted_change = datetime(2026, 5, 13, 9, 30, 0, tzinfo=timezone.utc)
        status.predicted_next_state = CrossingState.OPENING_PREDICTED
        status.reason = "train at crossing"

        history_db.log_prediction(
            status, feed_age_secs=2.5,
            active_routes=["R27", "R29"],
            config_hash="abc123def456",
        )

        db = sqlite3.connect(str(history_db.db_path))
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM predictions").fetchone()
        db.close()

        assert row["state"] == "closed_inferred"
        assert row["confidence"] == 0.95
        assert row["predicted_change_at"] == "2026-05-13T09:30:00+00:00"
        assert row["predicted_next_state"] == "opening_predicted"
        assert row["active_train_count"] == 1
        assert row["active_route_count"] == 2
        assert row["feed_age_secs"] == 2.5
        assert row["config_hash"] == "abc123def456"

    def test_route_info_objects_counted(self, history_db):
        from src.route_monitor import RouteInfo
        status = CrossingStatus()
        status.state = CrossingState.OPEN
        status.confidence = 0.8

        ts = datetime(2026, 5, 13, 9, 0, 0, tzinfo=timezone.utc)
        history_db.log_prediction(
            status,
            active_routes=[
                RouteInfo(name="R27", side="east", set_since=ts),
                RouteInfo(name="R29", side="east", set_since=ts),
            ],
        )

        db = sqlite3.connect(str(history_db.db_path))
        row = db.execute("SELECT active_route_count FROM predictions").fetchone()
        db.close()
        assert row[0] == 2

    def test_returns_timestamp_for_alignment(self, history_db):
        """log_prediction returns the timestamp it used so callers can align
        train_snapshots to the same tick."""
        status = CrossingStatus()
        status.state = CrossingState.OPEN
        ts = history_db.log_prediction(status)
        # Returned timestamp matches the row
        db = sqlite3.connect(str(history_db.db_path))
        row_ts = db.execute("SELECT timestamp FROM predictions").fetchone()[0]
        db.close()
        assert ts == row_ts

    def test_logs_every_tick_not_just_changes(self, history_db):
        status = CrossingStatus()
        status.state = CrossingState.OPEN
        status.confidence = 0.8
        # Log five times with no state change
        for _ in range(5):
            history_db.log_prediction(status)
        db = sqlite3.connect(str(history_db.db_path))
        n = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        db.close()
        assert n == 5

    def test_handles_missing_optional_fields(self, history_db):
        status = CrossingStatus()
        status.state = CrossingState.UNKNOWN
        # No confidence, no predicted_change, no trains, no routes
        history_db.log_prediction(status)
        db = sqlite3.connect(str(history_db.db_path))
        row = db.execute("SELECT * FROM predictions").fetchone()
        db.close()
        assert row is not None  # didn't crash

    def test_timestamp_has_microsecond_precision(self, history_db):
        import re
        status = CrossingStatus()
        status.state = CrossingState.OPEN
        history_db.log_prediction(status)
        db = sqlite3.connect(str(history_db.db_path))
        ts = db.execute("SELECT timestamp FROM predictions").fetchone()[0]
        db.close()
        # ISO format with microseconds: "2026-05-13T09:00:00.123456+00:00"
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}", ts)


class TestRouteIntervals:
    def test_creates_route_intervals_table(self, history_db):
        db = sqlite3.connect(str(history_db.db_path))
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        db.close()
        assert "route_intervals" in tables

    def test_unique_index_one_open_per_route(self, history_db):
        db = sqlite3.connect(str(history_db.db_path))
        indexes = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        db.close()
        assert "idx_route_intervals_one_open" in indexes

    def test_start_then_clear(self, history_db):
        t0 = datetime(2026, 5, 13, 9, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 5, 13, 9, 2, 30, tzinfo=timezone.utc)
        history_db.start_route_interval("R27", t0)
        history_db.close_route_interval("R27", t1, end_reason="observed_clear")

        db = sqlite3.connect(str(history_db.db_path))
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM route_intervals").fetchone()
        db.close()
        assert row["route_name"] == "R27"
        assert row["set_at"] == "2026-05-13T09:00:00+00:00"
        assert row["cleared_at"] == "2026-05-13T09:02:30+00:00"
        assert row["observed_until"] == "2026-05-13T09:02:30+00:00"
        assert row["end_reason"] == "observed_clear"
        assert abs(row["duration_secs"] - 150.0) < 0.01

    def test_disconnect_does_not_set_cleared_at(self, history_db):
        t0 = datetime(2026, 5, 13, 9, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 5, 13, 9, 5, 0, tzinfo=timezone.utc)
        history_db.start_route_interval("R27", t0)
        history_db.close_all_open_route_intervals(t1, end_reason="disconnect")

        db = sqlite3.connect(str(history_db.db_path))
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM route_intervals").fetchone()
        db.close()
        # cleared_at must be NULL — we did NOT observe a clear, just lost feed
        assert row["cleared_at"] is None
        assert row["observed_until"] == "2026-05-13T09:05:00+00:00"
        assert row["end_reason"] == "disconnect"

    def test_close_all_open_returns_count(self, history_db):
        t0 = datetime(2026, 5, 13, 9, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 5, 13, 9, 5, 0, tzinfo=timezone.utc)
        history_db.start_route_interval("R27", t0)
        history_db.start_route_interval("R29", t0)
        history_db.start_route_interval("RA007", t0)
        n = history_db.close_all_open_route_intervals(t1, end_reason="disconnect")
        assert n == 3

    def test_unique_index_rejects_double_set(self, history_db, caplog):
        t0 = datetime(2026, 5, 13, 9, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 5, 13, 9, 1, 0, tzinfo=timezone.utc)
        history_db.start_route_interval("R27", t0)
        # Second SET without an intervening CLEAR should be refused
        history_db.start_route_interval("R27", t1)

        db = sqlite3.connect(str(history_db.db_path))
        rows = db.execute("SELECT * FROM route_intervals").fetchall()
        db.close()
        # Only one row (the original) should exist
        assert len(rows) == 1

    def test_close_with_no_open_interval_is_safe(self, history_db):
        # Closing a route that was never opened should not crash
        history_db.close_route_interval(
            "R27", datetime(2026, 5, 13, 9, 0, 0, tzinfo=timezone.utc)
        )
        db = sqlite3.connect(str(history_db.db_path))
        rows = db.execute("SELECT * FROM route_intervals").fetchall()
        db.close()
        assert len(rows) == 0

    def test_close_only_affects_most_recent_open(self, history_db):
        # Set, clear, set again — close should only affect the second SET
        t0 = datetime(2026, 5, 13, 9, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 5, 13, 9, 2, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 5, 13, 9, 5, 0, tzinfo=timezone.utc)
        t3 = datetime(2026, 5, 13, 9, 7, 0, tzinfo=timezone.utc)
        history_db.start_route_interval("R27", t0)
        history_db.close_route_interval("R27", t1, end_reason="observed_clear")
        history_db.start_route_interval("R27", t2)
        history_db.close_route_interval("R27", t3, end_reason="observed_clear")

        db = sqlite3.connect(str(history_db.db_path))
        rows = db.execute(
            "SELECT set_at, cleared_at FROM route_intervals ORDER BY id"
        ).fetchall()
        db.close()
        assert len(rows) == 2
        assert rows[0] == ("2026-05-13T09:00:00+00:00", "2026-05-13T09:02:00+00:00")
        assert rows[1] == ("2026-05-13T09:05:00+00:00", "2026-05-13T09:07:00+00:00")

    def test_active_at_time_query(self, history_db):
        """The dominant analytical query: which routes were active at time T?"""
        # Set up: R27 active 09:00-09:05, R29 active 09:02-09:08
        t = lambda h, m: datetime(2026, 5, 13, h, m, 0, tzinfo=timezone.utc)
        history_db.start_route_interval("R27", t(9, 0))
        history_db.start_route_interval("R29", t(9, 2))
        history_db.close_route_interval("R27", t(9, 5), end_reason="observed_clear")
        history_db.close_route_interval("R29", t(9, 8), end_reason="observed_clear")

        # At 09:03, both should be active
        query_t = "2026-05-13T09:03:00+00:00"
        db = sqlite3.connect(str(history_db.db_path))
        rows = db.execute("""
            SELECT route_name FROM route_intervals
            WHERE set_at <= ?
              AND (
                end_reason IS NULL
                OR (end_reason = 'observed_clear' AND cleared_at > ?)
                OR (end_reason IN ('disconnect', 'startup_orphan') AND observed_until > ?)
              )
        """, (query_t, query_t, query_t)).fetchall()
        db.close()
        names = {r[0] for r in rows}
        assert names == {"R27", "R29"}


class TestRouteIntervalOrphanCleanup:
    def test_orphan_closed_at_startup(self, tmp_path):
        from src.history import HistoryLogger
        # Create logger, leave a route open
        h1 = HistoryLogger(db_path=tmp_path / "test.db")
        h1.start_route_interval("R27", datetime(2026, 5, 13, 9, 0, 0, tzinfo=timezone.utc))

        # Recreate logger — should close orphan
        h2 = HistoryLogger(db_path=tmp_path / "test.db")

        db = sqlite3.connect(str(tmp_path / "test.db"))
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM route_intervals").fetchone()
        db.close()
        # Orphan must be marked, not have an invented cleared_at
        assert row["end_reason"] == "startup_orphan"
        assert row["cleared_at"] is None
        assert row["observed_until"] is not None

    def test_orphan_cleanup_idempotent(self, tmp_path):
        from src.history import HistoryLogger
        h1 = HistoryLogger(db_path=tmp_path / "test.db")
        h1.start_route_interval("R27", datetime(2026, 5, 13, 9, 0, 0, tzinfo=timezone.utc))
        # Restart twice — second restart should not double-close
        HistoryLogger(db_path=tmp_path / "test.db")
        HistoryLogger(db_path=tmp_path / "test.db")
        db = sqlite3.connect(str(tmp_path / "test.db"))
        rows = db.execute("SELECT * FROM route_intervals").fetchall()
        db.close()
        assert len(rows) == 1  # still just one row


class TestLogTrainSnapshots:
    def test_creates_train_snapshots_table(self, history_db):
        db = sqlite3.connect(str(history_db.db_path))
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        db.close()
        assert "train_snapshots" in tables

    def test_indexes_exist(self, history_db):
        db = sqlite3.connect(str(history_db.db_path))
        indexes = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        db.close()
        assert "idx_train_snapshots_ts" in indexes
        assert "idx_train_snapshots_hc" in indexes

    def test_one_row_per_train(self, history_db, make_train):
        trains = [
            make_train(headcode="1A23", phase=TrainPhase.APPROACHING),
            make_train(headcode="2B45", phase=TrainPhase.AT_CROSSING),
            make_train(headcode="3C67", phase=TrainPhase.CLEARED),
        ]
        history_db.log_train_snapshots(trains, tick_timestamp="2026-05-13T09:00:00+00:00")

        db = sqlite3.connect(str(history_db.db_path))
        rows = db.execute(
            "SELECT headcode, phase FROM train_snapshots ORDER BY headcode"
        ).fetchall()
        db.close()
        assert rows == [("1A23", "approaching"), ("2B45", "at_crossing"), ("3C67", "cleared")]

    def test_captures_full_train_state(self, history_db, make_train):
        eta = datetime(2026, 5, 13, 9, 5, 0, tzinfo=timezone.utc)
        train = make_train(
            headcode="1A23", direction=Direction.UP,
            phase=TrainPhase.STRIKE_IN, last_berth="0040",
            confidence=0.85,
        )
        train.train_id = "202605131A23"
        train.predicted_at_crossing = eta

        history_db.log_train_snapshots([train], tick_timestamp="2026-05-13T09:00:00+00:00")

        db = sqlite3.connect(str(history_db.db_path))
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM train_snapshots").fetchone()
        db.close()
        assert row["tick_timestamp"] == "2026-05-13T09:00:00+00:00"
        assert row["headcode"] == "1A23"
        assert row["train_id"] == "202605131A23"
        assert row["direction"] == "up"
        assert row["phase"] == "strike_in"
        assert row["last_berth"] == "0040"
        assert row["predicted_at_crossing"] == "2026-05-13T09:05:00+00:00"
        assert row["confidence"] == 0.85
        assert row["first_seen"] is not None

    def test_empty_train_list_writes_nothing(self, history_db):
        history_db.log_train_snapshots([], tick_timestamp="2026-05-13T09:00:00+00:00")
        db = sqlite3.connect(str(history_db.db_path))
        n = db.execute("SELECT COUNT(*) FROM train_snapshots").fetchone()[0]
        db.close()
        assert n == 0

    def test_default_timestamp_used_when_omitted(self, history_db, make_train):
        train = make_train(headcode="1A23")
        history_db.log_train_snapshots([train])
        db = sqlite3.connect(str(history_db.db_path))
        row = db.execute("SELECT tick_timestamp FROM train_snapshots").fetchone()
        db.close()
        assert row[0] is not None  # something got stamped

    def test_alignment_with_predictions(self, history_db, make_train):
        """The pattern: log_prediction returns timestamp, pass to log_train_snapshots
        for exact JOIN alignment."""
        status = CrossingStatus()
        status.state = CrossingState.CLOSED_INFERRED
        status.active_trains = [make_train(headcode="1A23")]

        ts = history_db.log_prediction(status)
        history_db.log_train_snapshots(status.active_trains, tick_timestamp=ts)

        db = sqlite3.connect(str(history_db.db_path))
        # Verify the JOIN works
        row = db.execute(
            "SELECT p.state, ts.headcode FROM predictions p "
            "JOIN train_snapshots ts ON p.timestamp = ts.tick_timestamp"
        ).fetchone()
        db.close()
        assert row == ("closed_inferred", "1A23")

    def test_query_train_history(self, history_db, make_train):
        """Useful query: show all snapshots for a single train over time."""
        train = make_train(headcode="1A23", phase=TrainPhase.APPROACHING)
        history_db.log_train_snapshots([train], "2026-05-13T09:00:00+00:00")
        train.phase = TrainPhase.STRIKE_IN
        history_db.log_train_snapshots([train], "2026-05-13T09:01:00+00:00")
        train.phase = TrainPhase.AT_CROSSING
        history_db.log_train_snapshots([train], "2026-05-13T09:02:00+00:00")

        db = sqlite3.connect(str(history_db.db_path))
        rows = db.execute(
            "SELECT phase FROM train_snapshots WHERE headcode = ? "
            "ORDER BY tick_timestamp",
            ("1A23",),
        ).fetchall()
        db.close()
        assert [r[0] for r in rows] == ["approaching", "strike_in", "at_crossing"]
