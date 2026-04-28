"""Tests for CrossingInferrer state derivation logic."""

from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from src.models import CrossingState, TrainPhase, Direction

NOW = datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
FEED_RECENT = NOW - timedelta(seconds=10)


@freeze_time(NOW)
class TestNoTrains:
    def test_no_trains_returns_open(self, inferrer):
        status = inferrer.update([], FEED_RECENT)
        assert status.state == CrossingState.OPEN
        assert status.confidence == 0.8


@freeze_time(NOW)
class TestAtCrossing:
    def test_at_crossing_returns_closed(self, inferrer, make_train):
        train = make_train(phase=TrainPhase.AT_CROSSING)
        status = inferrer.update([train], FEED_RECENT)
        assert status.state == CrossingState.CLOSED_INFERRED
        assert status.confidence == 0.9
        assert status.predicted_change is not None
        assert status.predicted_next_state == CrossingState.OPENING_PREDICTED


@freeze_time(NOW)
class TestStrikeIn:
    def test_strike_in_returns_closing(self, inferrer, make_train):
        train = make_train(
            phase=TrainPhase.STRIKE_IN,
            predicted_at_crossing=NOW + timedelta(seconds=60),
        )
        status = inferrer.update([train], FEED_RECENT)
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert status.confidence == 0.8


@freeze_time(NOW)
class TestAtStation:
    def test_at_station_returns_closing(self, inferrer, make_train):
        train = make_train(
            phase=TrainPhase.AT_STATION,
            predicted_at_crossing=NOW + timedelta(seconds=90),
        )
        status = inferrer.update([train], FEED_RECENT)
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert status.confidence == 0.8


@freeze_time(NOW)
class TestApproachingClose:
    def test_approaching_within_pre_closure_returns_closing(self, inferrer, make_train):
        train = make_train(
            phase=TrainPhase.APPROACHING,
            predicted_at_crossing=NOW + timedelta(seconds=60),
        )
        status = inferrer.update([train], FEED_RECENT)
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert status.confidence == 0.6


@freeze_time(NOW)
class TestApproachingDistant:
    def test_approaching_distant_returns_open(self, inferrer, make_train):
        train = make_train(
            phase=TrainPhase.APPROACHING,
            predicted_at_crossing=NOW + timedelta(seconds=300),
        )
        status = inferrer.update([train], FEED_RECENT)
        assert status.state == CrossingState.OPEN
        assert status.confidence == 0.7
        assert status.predicted_change is not None
        assert status.predicted_next_state == CrossingState.CLOSING_PREDICTED


@freeze_time(NOW)
class TestStaleFeed:
    def test_stale_feed_returns_stale(self, inferrer, make_train):
        old_feed = NOW - timedelta(seconds=400)
        status = inferrer.update([], old_feed)
        assert status.state == CrossingState.STALE_DATA
        assert status.confidence == 0.3

    def test_none_feed_no_trains_returns_open(self, inferrer):
        status = inferrer.update([], None)
        assert status.state == CrossingState.OPEN
        assert status.confidence == 0.8


@freeze_time(NOW)
class TestMultiTrain:
    def test_cleared_plus_approaching(self, inferrer, make_train):
        cleared = make_train(headcode="1X00", phase=TrainPhase.CLEARED)
        approaching = make_train(
            headcode="2Y00",
            phase=TrainPhase.APPROACHING,
            predicted_at_crossing=NOW + timedelta(seconds=60),
        )
        status = inferrer.update([cleared, approaching], FEED_RECENT)
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert status.confidence == 0.6


@freeze_time(NOW)
class TestTransition:
    def test_state_change_resets_since(self, inferrer, make_train):
        inferrer.update([], FEED_RECENT)
        since_open = inferrer.status.since

        train = make_train(phase=TrainPhase.AT_CROSSING)
        inferrer.update([train], FEED_RECENT)
        since_closed = inferrer.status.since

        assert since_closed >= since_open
        assert inferrer.status.state == CrossingState.CLOSED_INFERRED

    def test_same_state_keeps_since(self, inferrer):
        inferrer.update([], FEED_RECENT)
        since_first = inferrer.status.since

        inferrer.update([], FEED_RECENT)
        since_second = inferrer.status.since

        assert since_first == since_second


@freeze_time(NOW)
class TestPredictedNextState:
    def test_closed_predicts_opening(self, inferrer, make_train):
        train = make_train(phase=TrainPhase.AT_CROSSING)
        status = inferrer.update([train], FEED_RECENT)
        assert status.predicted_next_state == CrossingState.OPENING_PREDICTED

    def test_closing_predicts_closed(self, inferrer, make_train):
        train = make_train(
            phase=TrainPhase.STRIKE_IN,
            predicted_at_crossing=NOW + timedelta(seconds=60),
        )
        status = inferrer.update([train], FEED_RECENT)
        assert status.predicted_next_state == CrossingState.CLOSED_INFERRED
