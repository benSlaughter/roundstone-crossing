"""Tests for src/models — enums, TrackedTrain, and CrossingStatus."""

from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from src.models import (
    CrossingState,
    CrossingStatus,
    Direction,
    TrackedTrain,
    TrainPhase,
)


# ── Enum values ──────────────────────────────────────────────────────

class TestCrossingState:
    @pytest.mark.parametrize("member, expected", [
        (CrossingState.UNKNOWN, "unknown"),
        (CrossingState.OPEN, "open"),
        (CrossingState.CLOSING_PREDICTED, "closing_predicted"),
        (CrossingState.CLOSED_INFERRED, "closed_inferred"),
        (CrossingState.OPENING_PREDICTED, "opening_predicted"),
        (CrossingState.STALE_DATA, "stale_data"),
    ])
    def test_values(self, member, expected):
        assert member.value == expected


class TestDirection:
    @pytest.mark.parametrize("member, expected", [
        (Direction.UP, "up"),
        (Direction.DOWN, "down"),
    ])
    def test_values(self, member, expected):
        assert member.value == expected


class TestTrainPhase:
    @pytest.mark.parametrize("member, expected", [
        (TrainPhase.APPROACHING, "approaching"),
        (TrainPhase.STRIKE_IN, "strike_in"),
        (TrainPhase.AT_CROSSING, "at_crossing"),
        (TrainPhase.CLEARED, "cleared"),
        (TrainPhase.AT_STATION, "at_station"),
        (TrainPhase.LOST, "lost"),
    ])
    def test_values(self, member, expected):
        assert member.value == expected


# ── TrackedTrain ─────────────────────────────────────────────────────

class TestTrackedTrainDefaults:
    def test_required_headcode(self):
        train = TrackedTrain(headcode="1A23")
        assert train.headcode == "1A23"

    def test_defaults(self, make_train):
        train = make_train()
        assert train.phase == TrainPhase.APPROACHING
        assert train.confidence == 0.5
        assert train.train_id is None
        assert train.last_berth is None
        assert train.predicted_at_crossing is None
        assert train.station is None
        assert train.sub_position is None

    def test_first_seen_and_last_update_set_automatically(self):
        train = TrackedTrain(headcode="1X99")
        assert isinstance(train.first_seen, datetime)
        assert isinstance(train.last_update, datetime)
        assert train.first_seen.tzinfo is not None


class TestTrackedTrainAgeSecs:
    @freeze_time("2025-06-15 10:30:00", tz_offset=0)
    def test_age_secs_fresh(self):
        train = TrackedTrain(headcode="1A23")
        assert train.age_secs == pytest.approx(0.0, abs=1)

    @freeze_time("2025-06-15 10:30:00", tz_offset=0)
    def test_age_secs_with_old_update(self):
        old = datetime(2025, 6, 15, 10, 28, 0, tzinfo=timezone.utc)
        train = TrackedTrain(headcode="1A23", last_update=old)
        assert train.age_secs == pytest.approx(120.0, abs=1)


class TestTrackedTrainIsStale:
    @freeze_time("2025-06-15 10:30:00", tz_offset=0)
    def test_not_stale_at_120s(self):
        update_time = datetime(2025, 6, 15, 10, 28, 0, tzinfo=timezone.utc)
        train = TrackedTrain(headcode="1A23", last_update=update_time)
        assert train.is_stale is False

    @freeze_time("2025-06-15 10:30:00", tz_offset=0)
    def test_stale_after_120s(self):
        update_time = datetime(2025, 6, 15, 10, 27, 59, tzinfo=timezone.utc)
        train = TrackedTrain(headcode="1A23", last_update=update_time)
        assert train.is_stale is True


# ── CrossingStatus ───────────────────────────────────────────────────

class TestCrossingStatusDefaults:
    def test_defaults(self):
        status = CrossingStatus()
        assert status.state == CrossingState.UNKNOWN
        assert status.confidence == 0.0
        assert status.active_trains == []
        assert status.predicted_change is None
        assert status.predicted_next_state is None
        assert status.last_feed_message is None


class TestSecondsUntilChange:
    def test_none_when_no_predicted_change(self):
        status = CrossingStatus()
        assert status.seconds_until_change() is None

    @freeze_time("2025-06-15 10:30:00", tz_offset=0)
    def test_positive_when_in_future(self):
        future = datetime(2025, 6, 15, 10, 31, 0, tzinfo=timezone.utc)
        status = CrossingStatus(predicted_change=future)
        assert status.seconds_until_change() == pytest.approx(60.0, abs=1)

    @freeze_time("2025-06-15 10:30:00", tz_offset=0)
    def test_zero_when_in_past(self):
        past = datetime(2025, 6, 15, 10, 29, 0, tzinfo=timezone.utc)
        status = CrossingStatus(predicted_change=past)
        assert status.seconds_until_change() == 0.0


class TestToDict:
    @freeze_time("2025-06-15 10:30:00", tz_offset=0)
    def test_keys_present(self):
        status = CrossingStatus()
        d = status.to_dict()
        expected_keys = {
            "state", "confidence", "reason", "since", "seconds_in_state",
            "predicted_change", "seconds_until_change",
            "predicted_next_state", "active_trains",
        }
        assert set(d.keys()) == expected_keys

    @freeze_time("2025-06-15 10:30:00", tz_offset=0)
    def test_types(self):
        status = CrossingStatus()
        d = status.to_dict()
        assert isinstance(d["state"], str)
        assert isinstance(d["confidence"], float)
        assert isinstance(d["since"], str)
        assert isinstance(d["seconds_in_state"], (int, float))
        assert isinstance(d["active_trains"], list)

    @freeze_time("2025-06-15 10:30:00", tz_offset=0)
    def test_with_active_train(self):
        train = TrackedTrain(headcode="1A23", direction=Direction.UP)
        status = CrossingStatus(
            state=CrossingState.CLOSING_PREDICTED,
            confidence=0.8,
            active_trains=[train],
            predicted_change=datetime(2025, 6, 15, 10, 31, 0, tzinfo=timezone.utc),
            predicted_next_state=CrossingState.CLOSED_INFERRED,
        )
        d = status.to_dict()
        assert d["state"] == "closing_predicted"
        assert d["confidence"] == 0.8
        assert d["predicted_next_state"] == "closed_inferred"
        assert d["seconds_until_change"] == 60
        assert len(d["active_trains"]) == 1
        assert d["active_trains"][0]["headcode"] == "1A23"
        assert d["active_trains"][0]["direction"] == "up"
        assert d["active_trains"][0]["phase"] == "approaching"

    @freeze_time("2025-06-15 10:30:00", tz_offset=0)
    def test_none_fields_when_no_prediction(self):
        status = CrossingStatus()
        d = status.to_dict()
        assert d["predicted_change"] is None
        assert d["seconds_until_change"] is None
        assert d["predicted_next_state"] is None
