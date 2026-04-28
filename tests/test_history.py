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
        for t in ("state_intervals", "train_passages", "raw_events", "train_events"):
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
            history_db.log_train_event("1A23", "berth_step", "A027", "0040")
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
