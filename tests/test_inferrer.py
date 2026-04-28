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


@freeze_time(NOW)
class TestMultiTrainOpening:
    """Tests for multi-train-aware opening prediction."""

    def test_single_at_crossing_predicts_opening(self, inferrer, make_train):
        """Single train at crossing: opening = now + crossing_clearance + post_clearance."""
        train = make_train(phase=TrainPhase.AT_CROSSING)
        status = inferrer.update([train], FEED_RECENT)
        assert status.predicted_change is not None
        # crossing_clearance_secs=10 + post_clearance_secs=8 = 18s
        expected = NOW + timedelta(seconds=10 + 8)
        assert abs((status.predicted_change - expected).total_seconds()) < 2

    def test_two_close_trains_merge_windows(self, inferrer, make_train):
        """Two trains with overlapping closure windows → merged, opening after last clears."""
        at_crossing = make_train(headcode="1A00", phase=TrainPhase.AT_CROSSING)
        # Second train arriving 30s after now — windows overlap
        strike_in = make_train(
            headcode="2B00",
            phase=TrainPhase.STRIKE_IN,
            predicted_at_crossing=NOW + timedelta(seconds=30),
        )
        status = inferrer.update([at_crossing, strike_in], FEED_RECENT)
        assert status.predicted_change is not None
        # Opening should be after the SECOND train clears:
        # strike_in clears at NOW+30+10=NOW+40, plus post_clearance=8 → NOW+48
        expected = NOW + timedelta(seconds=30 + 10 + 8)
        assert abs((status.predicted_change - expected).total_seconds()) < 2

    def test_two_distant_trains_first_determines_opening(self, inferrer, make_train):
        """Train at crossing + distant approaching train → opening after first train.
        
        Distant train's closure window doesn't overlap → gap exists, 
        opening predicted after the current block ends.
        """
        at_crossing = make_train(headcode="1A00", phase=TrainPhase.AT_CROSSING)
        # Second train 5 minutes away — window does NOT overlap
        distant = make_train(
            headcode="2B00",
            phase=TrainPhase.APPROACHING,
            predicted_at_crossing=NOW + timedelta(seconds=300),
        )
        status = inferrer.update([at_crossing, distant], FEED_RECENT)
        assert status.predicted_change is not None
        # Opening should be after the FIRST train only:
        # AT_CROSSING clears at NOW+10, plus post_clearance=8 → NOW+18
        expected = NOW + timedelta(seconds=10 + 8)
        assert abs((status.predicted_change - expected).total_seconds()) < 2

    def test_at_station_without_eta_excluded(self, inferrer, make_train):
        """AT_STATION train without predicted_at_crossing is excluded from prediction."""
        at_crossing = make_train(headcode="1A00", phase=TrainPhase.AT_CROSSING)
        at_station = make_train(
            headcode="2B00",
            phase=TrainPhase.AT_STATION,
            predicted_at_crossing=None,
        )
        status = inferrer.update([at_crossing, at_station], FEED_RECENT)
        # Should only consider the AT_CROSSING train
        expected = NOW + timedelta(seconds=10 + 8)
        assert abs((status.predicted_change - expected).total_seconds()) < 2

    def test_strike_in_with_eta_extends_closure(self, inferrer, make_train):
        """STRIKE_IN train with predicted_at_crossing extends the closure window."""
        at_crossing = make_train(headcode="1A00", phase=TrainPhase.AT_CROSSING)
        strike_in = make_train(
            headcode="2B00",
            phase=TrainPhase.STRIKE_IN,
            predicted_at_crossing=NOW + timedelta(seconds=20),
        )
        status = inferrer.update([at_crossing, strike_in], FEED_RECENT)
        # Strike-in clears at NOW+20+10=NOW+30, plus post_clearance=8 → NOW+38
        expected = NOW + timedelta(seconds=20 + 10 + 8)
        assert abs((status.predicted_change - expected).total_seconds()) < 2


@freeze_time(NOW)
class TestOpeningPredictedState:
    """Tests for the OPENING_PREDICTED transitional state."""

    def test_opening_predicted_after_recent_clear(self, inferrer, make_train):
        """When trains were present, then cleared, show OPENING_PREDICTED briefly."""
        # First: train at crossing → CLOSED_INFERRED
        train = make_train(phase=TrainPhase.AT_CROSSING)
        inferrer.update([train], FEED_RECENT)
        assert inferrer.status.state == CrossingState.CLOSED_INFERRED

        # Then: train cleared (empty active list, but within post_clearance window)
        status = inferrer.update([], FEED_RECENT)
        assert status.state == CrossingState.OPENING_PREDICTED
        assert status.predicted_next_state == CrossingState.OPEN
        assert status.predicted_change is not None

    def test_open_after_clearance_window(self, inferrer, make_train):
        """After post_clearance window expires, state goes to OPEN."""
        # No previous trains tracked → no _last_clear_time
        status = inferrer.update([], FEED_RECENT)
        assert status.state == CrossingState.OPEN

    def test_opening_predicted_clears_to_open(self, inferrer, make_train):
        """OPENING_PREDICTED should have predicted_change pointing to when OPEN starts."""
        train = make_train(phase=TrainPhase.AT_CROSSING)
        inferrer.update([train], FEED_RECENT)

        status = inferrer.update([], FEED_RECENT)
        assert status.state == CrossingState.OPENING_PREDICTED
        # predicted_change = _last_clear_time + post_clearance_secs
        # _last_clear_time was set to NOW when trains were active
        expected_open = NOW + timedelta(seconds=8)  # post_clearance_secs
        assert abs((status.predicted_change - expected_open).total_seconds()) < 2


@freeze_time(NOW)
class TestBarriersStayClosedForConsecutiveTrains:
    """Barriers should not bounce CLOSED → CLOSING → CLOSED for back-to-back trains."""

    def test_closed_stays_closed_when_next_train_in_strike_in(self, inferrer, make_train):
        """After AT_CROSSING train clears, a STRIKE_IN train keeps barriers down."""
        at_crossing = make_train(headcode="1A00", phase=TrainPhase.AT_CROSSING)
        inferrer.update([at_crossing], FEED_RECENT)
        assert inferrer.status.state == CrossingState.CLOSED_INFERRED

        # First train clears, second is in strike-in
        strike_in = make_train(
            headcode="2B00",
            phase=TrainPhase.STRIKE_IN,
            predicted_at_crossing=NOW + timedelta(seconds=30),
        )
        status = inferrer.update([strike_in], FEED_RECENT)
        # Should stay CLOSED, not bounce to CLOSING_PREDICTED
        assert status.state == CrossingState.CLOSED_INFERRED

    def test_closed_stays_closed_when_next_train_at_station(self, inferrer, make_train):
        """After AT_CROSSING, an AT_STATION train keeps barriers down."""
        at_crossing = make_train(headcode="1A00", phase=TrainPhase.AT_CROSSING)
        inferrer.update([at_crossing], FEED_RECENT)
        assert inferrer.status.state == CrossingState.CLOSED_INFERRED

        at_station = make_train(
            headcode="2B00",
            phase=TrainPhase.AT_STATION,
            predicted_at_crossing=NOW + timedelta(seconds=60),
        )
        status = inferrer.update([at_station], FEED_RECENT)
        assert status.state == CrossingState.CLOSED_INFERRED

    def test_fresh_strike_in_without_prior_closed_shows_closing(self, inferrer, make_train):
        """STRIKE_IN from OPEN state should still show CLOSING_PREDICTED."""
        strike_in = make_train(
            phase=TrainPhase.STRIKE_IN,
            predicted_at_crossing=NOW + timedelta(seconds=30),
        )
        status = inferrer.update([strike_in], FEED_RECENT)
        assert status.state == CrossingState.CLOSING_PREDICTED
