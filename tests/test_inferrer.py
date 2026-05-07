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
        # crossing_clearance_secs=10 + post_clearance_secs=5 = 15s
        expected = NOW + timedelta(seconds=10 + 5)
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
        expected = NOW + timedelta(seconds=30 + 10 + 5)
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
        expected = NOW + timedelta(seconds=10 + 5)
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
        expected = NOW + timedelta(seconds=10 + 5)
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
        expected = NOW + timedelta(seconds=20 + 10 + 5)
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
        expected_open = NOW + timedelta(seconds=5)  # post_clearance_secs
        assert abs((status.predicted_change - expected_open).total_seconds()) < 2

    def test_closing_to_clear_skips_opening_predicted(self, inferrer, make_train):
        """If trains vanish from CLOSING_PREDICTED (never closed), go straight to OPEN."""
        train = make_train(
            phase=TrainPhase.STRIKE_IN,
            predicted_at_crossing=NOW + timedelta(seconds=30),
        )
        inferrer.update([train], FEED_RECENT)
        assert inferrer.status.state == CrossingState.CLOSING_PREDICTED

        # Train disappears (cleared/lost) — barriers never actually closed
        status = inferrer.update([], FEED_RECENT)
        assert status.state == CrossingState.OPEN  # NOT OPENING_PREDICTED


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

    def test_closed_stays_closed_when_approaching_within_pre_closure(self, inferrer, make_train):
        """After AT_CROSSING, an APPROACHING train within pre_closure keeps barriers down."""
        at_crossing = make_train(headcode="1A00", phase=TrainPhase.AT_CROSSING)
        inferrer.update([at_crossing], FEED_RECENT)
        assert inferrer.status.state == CrossingState.CLOSED_INFERRED

        approaching = make_train(
            headcode="2B00",
            phase=TrainPhase.APPROACHING,
            predicted_at_crossing=NOW + timedelta(seconds=60),
        )
        status = inferrer.update([approaching], FEED_RECENT)
        # Should stay CLOSED — barriers wouldn't open and reclose
        assert status.state == CrossingState.CLOSED_INFERRED


@freeze_time(NOW)
class TestRouteEnhancedPrediction:
    """Tests for route-enhanced prediction — SF route data boosts confidence."""

    def test_at_crossing_with_routes_higher_confidence(self, inferrer, make_train):
        """AT_CROSSING + routes SET → 0.95 confidence (was 0.9 without routes)."""
        train = make_train(phase=TrainPhase.AT_CROSSING)
        status = inferrer.update([train], FEED_RECENT, active_routes=["R35"])
        assert status.state == CrossingState.CLOSED_INFERRED
        assert status.confidence == 0.95

    def test_at_crossing_without_routes_normal_confidence(self, inferrer, make_train):
        """AT_CROSSING without routes → 0.9 confidence (unchanged)."""
        train = make_train(phase=TrainPhase.AT_CROSSING)
        status = inferrer.update([train], FEED_RECENT, active_routes=[])
        assert status.state == CrossingState.CLOSED_INFERRED
        assert status.confidence == 0.9

    def test_strike_in_with_routes_infers_closed(self, inferrer, make_train):
        """STRIKE_IN + routes SET → CLOSED_INFERRED at 0.9 (not CLOSING_PREDICTED)."""
        train = make_train(
            phase=TrainPhase.STRIKE_IN,
            predicted_at_crossing=NOW + timedelta(seconds=60),
        )
        status = inferrer.update([train], FEED_RECENT, active_routes=["R32"])
        assert status.state == CrossingState.CLOSED_INFERRED
        assert status.confidence == 0.9

    def test_strike_in_without_routes_predicts_closing(self, inferrer, make_train):
        """STRIKE_IN without routes → CLOSING_PREDICTED at 0.8 (unchanged)."""
        train = make_train(
            phase=TrainPhase.STRIKE_IN,
            predicted_at_crossing=NOW + timedelta(seconds=60),
        )
        status = inferrer.update([train], FEED_RECENT, active_routes=[])
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert status.confidence == 0.8

    def test_approaching_with_routes_higher_confidence(self, inferrer, make_train):
        """APPROACHING + routes SET → CLOSING_PREDICTED at 0.85 (was 0.6)."""
        train = make_train(
            phase=TrainPhase.APPROACHING,
            predicted_at_crossing=NOW + timedelta(seconds=60),
        )
        status = inferrer.update([train], FEED_RECENT, active_routes=["RA007"])
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert status.confidence == 0.85

    def test_routes_only_no_trains_predicts_closing(self, inferrer):
        """Routes SET but no trains visible → CLOSING_PREDICTED at 0.7 (early warning)."""
        status = inferrer.update([], FEED_RECENT, active_routes=["R35"])
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert status.confidence == 0.7

    def test_no_routes_no_trains_stays_open(self, inferrer):
        """No routes, no trains → OPEN (unchanged)."""
        status = inferrer.update([], FEED_RECENT, active_routes=[])
        assert status.state == CrossingState.OPEN

    def test_was_closed_routes_active_stays_closed(self, inferrer, make_train):
        """Previously closed + routes still SET → stay CLOSED at 0.9."""
        train = make_train(phase=TrainPhase.AT_CROSSING)
        inferrer.update([train], FEED_RECENT)
        assert inferrer.status.state == CrossingState.CLOSED_INFERRED

        # Train clears, but keep approaching train + routes
        approaching = make_train(
            headcode="2B00",
            phase=TrainPhase.APPROACHING,
            predicted_at_crossing=NOW + timedelta(seconds=120),
        )
        status = inferrer.update([approaching], FEED_RECENT, active_routes=["R35"])
        assert status.state == CrossingState.CLOSED_INFERRED
        assert status.confidence == 0.9

    def test_none_routes_backward_compat(self, inferrer, make_train):
        """active_routes=None (not provided) → same behaviour as before."""
        train = make_train(phase=TrainPhase.AT_CROSSING)
        status = inferrer.update([train], FEED_RECENT)
        assert status.state == CrossingState.CLOSED_INFERRED
        assert status.confidence == 0.9

    def test_routes_prevent_premature_opening(self, inferrer, make_train):
        """After all trains clear, routes still SET → don't predict OPENING yet."""
        train = make_train(phase=TrainPhase.AT_CROSSING)
        inferrer.update([train], FEED_RECENT, active_routes=["R35"])

        # All trains gone, but routes still active
        status = inferrer.update([], FEED_RECENT, active_routes=["R35"])
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert status.confidence == 0.7

    def test_routes_clear_then_opening(self, inferrer, make_train):
        """After trains AND routes clear → normal OPENING_PREDICTED flow."""
        train = make_train(phase=TrainPhase.AT_CROSSING)
        inferrer.update([train], FEED_RECENT, active_routes=["R35"])

        # All trains and routes cleared
        status = inferrer.update([], FEED_RECENT, active_routes=[])
        assert status.state == CrossingState.OPENING_PREDICTED


@freeze_time(NOW)
class TestRouteOnlyOpeningPredicted:
    """Tests for OPENING_PREDICTED triggered by route-clear alone (no AT_CROSSING ever).

    For MCB-CCTV crossings, the signaller cannot set a route until barriers are
    down + CCTV verified. So route SET implies barriers down. When the route
    eventually clears with no train ever appearing in our berth zone (e.g. the
    train route bypassed our zone, or we missed the steps), we should still
    transition through OPENING_PREDICTED briefly — not jump straight to OPEN.
    """

    def test_route_only_clear_emits_opening_predicted(self, inferrer):
        """Routes set with no trains, then routes clear → OPENING_PREDICTED."""
        # Tick 1: routes set, no trains → CLOSING_PREDICTED via route-only branch
        status = inferrer.update([], FEED_RECENT, active_routes=["R35"])
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert status.confidence == 0.7

        # Tick 2: routes clear, still no trains → OPENING_PREDICTED (not OPEN)
        status = inferrer.update([], FEED_RECENT, active_routes=[])
        assert status.state == CrossingState.OPENING_PREDICTED
        assert status.predicted_next_state == CrossingState.OPEN
        assert status.predicted_change is not None

    def test_no_routes_initial_state_no_opening(self, inferrer):
        """No prior routes, then call with no routes → straight to OPEN, no OPENING flicker."""
        status = inferrer.update([], FEED_RECENT, active_routes=[])
        assert status.state == CrossingState.OPEN

    def test_route_only_then_clear_then_open_after_window(self, inferrer):
        """OPENING_PREDICTED expires after post_clearance_secs → OPEN."""
        with freeze_time(NOW) as frozen:
            inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])
            inferrer.update([], datetime.now(timezone.utc), active_routes=[])
            # Immediately after route clear: OPENING_PREDICTED
            assert inferrer.status.state == CrossingState.OPENING_PREDICTED

            # Advance past the post_clearance window (5s in test config)
            frozen.tick(delta=timedelta(seconds=10))
            status = inferrer.update([], datetime.now(timezone.utc), active_routes=[])
            assert status.state == CrossingState.OPEN


@freeze_time(NOW)
class TestRouteHoldCap:
    """Tests for the route-hold cap: route-only inference shouldn't last forever.

    Stuck routes (240s lock-after-cancel, signal failure, signaller anomaly)
    can persist with no train ever passing. After max_route_hold_secs, we
    downgrade to UNKNOWN rather than asserting CLOSED indefinitely.
    """

    def test_short_route_hold_stays_closing_predicted(self, inferrer):
        """Route held briefly (< cap) → stays CLOSING_PREDICTED."""
        with freeze_time(NOW) as frozen:
            status = inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])
            assert status.state == CrossingState.CLOSING_PREDICTED

            frozen.tick(delta=timedelta(seconds=300))  # 5 min, under the cap
            status = inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])
            assert status.state == CrossingState.CLOSING_PREDICTED
            assert status.confidence == 0.7

    def test_long_route_hold_downgrades_to_unknown(self, inferrer):
        """Route held > max_route_hold_secs → UNKNOWN with low confidence."""
        with freeze_time(NOW) as frozen:
            inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])
            assert inferrer.status.state == CrossingState.CLOSING_PREDICTED

            # Cap is 900s in test config; advance past it
            frozen.tick(delta=timedelta(seconds=901))
            status = inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])
            assert status.state == CrossingState.UNKNOWN
            assert status.confidence == 0.3

    def test_route_hold_resets_when_train_appears(self, inferrer, make_train):
        """If a train appears, the routes-only timer resets — long subsequent route holds OK."""
        with freeze_time(NOW) as frozen:
            inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])

            # Cap nearly hit; train arrives in time to reset the timer
            frozen.tick(delta=timedelta(seconds=890))
            train = make_train(phase=TrainPhase.AT_CROSSING)
            inferrer.update([train], datetime.now(timezone.utc), active_routes=["R35"])
            assert inferrer.status.state == CrossingState.CLOSED_INFERRED

            # Train clears, routes still active — back to route-only branch.
            # Per existing behaviour (test_routes_prevent_premature_opening), we
            # downgrade to CLOSING_PREDICTED 0.7 when no trains + routes only.
            # The cap timer restarts here because _routes_only_since was reset.
            frozen.tick(delta=timedelta(seconds=10))
            status = inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])
            assert status.state == CrossingState.CLOSING_PREDICTED
            assert status.confidence == 0.7

            # Hitting the cap requires another full max_route_hold_secs from now.
            # Half the cap → still CLOSING_PREDICTED.
            frozen.tick(delta=timedelta(seconds=450))
            status = inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])
            assert status.state == CrossingState.CLOSING_PREDICTED

            # Past the cap → UNKNOWN.
            frozen.tick(delta=timedelta(seconds=500))
            status = inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])
            assert status.state == CrossingState.UNKNOWN

    def test_route_hold_resets_when_routes_clear(self, inferrer):
        """When routes briefly clear and re-set, the timer restarts."""
        with freeze_time(NOW) as frozen:
            inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])

            # Routes briefly clear
            frozen.tick(delta=timedelta(seconds=500))
            inferrer.update([], datetime.now(timezone.utc), active_routes=[])

            # Routes set again — fresh start, well under cap
            frozen.tick(delta=timedelta(seconds=600))
            status = inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])
            assert status.state == CrossingState.CLOSING_PREDICTED

            # Need full cap from this point to hit UNKNOWN
            frozen.tick(delta=timedelta(seconds=400))
            status = inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])
            assert status.state == CrossingState.CLOSING_PREDICTED  # 400s held, under 900s cap


@freeze_time(NOW)
class TestStateReason:
    """Every state transition should record a human-readable reason explaining
    why that state was entered. Useful for /live debug view and history audit.

    These tests verify that meaningful reason strings are populated on every
    code path. They check for substrings rather than exact text so the wording
    can be tweaked without breaking tests.
    """

    def test_open_no_trains_no_routes_reason(self, inferrer):
        status = inferrer.update([], FEED_RECENT, active_routes=[])
        assert status.state == CrossingState.OPEN
        assert status.reason is not None
        assert "no trains" in status.reason.lower()

    def test_at_crossing_reason_includes_headcode(self, inferrer, make_train):
        train = make_train(headcode="1A23", phase=TrainPhase.AT_CROSSING)
        status = inferrer.update([train], FEED_RECENT, active_routes=[])
        assert status.state == CrossingState.CLOSED_INFERRED
        assert "1A23" in status.reason
        assert "at crossing" in status.reason.lower()

    def test_at_crossing_with_routes_reason_mentions_routes(self, inferrer, make_train):
        train = make_train(phase=TrainPhase.AT_CROSSING)
        status = inferrer.update([train], FEED_RECENT, active_routes=["R35", "RA007"])
        assert status.state == CrossingState.CLOSED_INFERRED
        assert "R35" in status.reason or "RA007" in status.reason
        assert "route" in status.reason.lower()

    def test_strike_in_with_routes_reason(self, inferrer, make_train):
        train = make_train(headcode="2B45", phase=TrainPhase.STRIKE_IN,
                           predicted_at_crossing=NOW + timedelta(seconds=60))
        status = inferrer.update([train], FEED_RECENT, active_routes=["R32"])
        assert status.state == CrossingState.CLOSED_INFERRED
        assert "2B45" in status.reason
        assert "R32" in status.reason
        assert "strike-in" in status.reason.lower()

    def test_strike_in_without_routes_reason(self, inferrer, make_train):
        train = make_train(headcode="2B45", phase=TrainPhase.STRIKE_IN,
                           predicted_at_crossing=NOW + timedelta(seconds=60))
        status = inferrer.update([train], FEED_RECENT, active_routes=[])
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert "2B45" in status.reason
        assert "no route" in status.reason.lower()

    def test_approaching_with_routes_reason(self, inferrer, make_train):
        train = make_train(headcode="1A23", phase=TrainPhase.APPROACHING,
                           predicted_at_crossing=NOW + timedelta(seconds=200))
        status = inferrer.update([train], FEED_RECENT, active_routes=["R29"])
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert "1A23" in status.reason
        assert "R29" in status.reason
        assert "approaching" in status.reason.lower()

    def test_approaching_within_pre_closure_reason(self, inferrer, make_train):
        train = make_train(headcode="1A23", phase=TrainPhase.APPROACHING,
                           predicted_at_crossing=NOW + timedelta(seconds=60))
        status = inferrer.update([train], FEED_RECENT, active_routes=[])
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert "1A23" in status.reason
        assert "pre_closure" in status.reason.lower() or "60s" in status.reason

    def test_approaching_distant_reason(self, inferrer, make_train):
        train = make_train(headcode="1A23", phase=TrainPhase.APPROACHING,
                           predicted_at_crossing=NOW + timedelta(seconds=600))
        status = inferrer.update([train], FEED_RECENT, active_routes=[])
        assert status.state == CrossingState.OPEN
        assert "1A23" in status.reason
        assert "outside" in status.reason.lower()

    def test_route_only_reason_lists_routes(self, inferrer):
        status = inferrer.update([], FEED_RECENT, active_routes=["R35", "RA007"])
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert "R35" in status.reason
        assert "RA007" in status.reason
        assert "no train" in status.reason.lower()

    def test_route_hold_timeout_reason(self, inferrer):
        with freeze_time(NOW) as frozen:
            inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])
            frozen.tick(delta=timedelta(seconds=901))
            status = inferrer.update([], datetime.now(timezone.utc), active_routes=["R35"])
            assert status.state == CrossingState.UNKNOWN
            assert "stuck" in status.reason.lower() or "timeout" in status.reason.lower()
            assert "R35" in status.reason

    def test_stale_data_reason(self, inferrer, make_train):
        very_old_feed = NOW - timedelta(seconds=400)
        status = inferrer.update([], very_old_feed, active_routes=[])
        assert status.state == CrossingState.STALE_DATA
        assert "feed" in status.reason.lower()

    def test_opening_predicted_reason(self, inferrer, make_train):
        # Train at crossing, then clears
        train = make_train(phase=TrainPhase.AT_CROSSING)
        inferrer.update([train], FEED_RECENT, active_routes=[])
        status = inferrer.update([], FEED_RECENT, active_routes=[])
        assert status.state == CrossingState.OPENING_PREDICTED
        assert "post-clearance" in status.reason.lower() or "cctv" in status.reason.lower()

    def test_was_closed_reason_mentions_held(self, inferrer, make_train):
        # Get into CLOSED_INFERRED first
        train = make_train(headcode="1A23", phase=TrainPhase.AT_CROSSING)
        inferrer.update([train], FEED_RECENT, active_routes=[])
        # Now follow with a strike-in train (simulating consecutive trains): was_closed branch fires
        next_train = make_train(headcode="2B45", phase=TrainPhase.STRIKE_IN,
                                predicted_at_crossing=NOW + timedelta(seconds=60))
        status = inferrer.update([next_train], FEED_RECENT, active_routes=[])
        assert status.state == CrossingState.CLOSED_INFERRED
        assert "held closed" in status.reason.lower() or "still active" in status.reason.lower()

    def test_reason_is_set_on_every_state(self, inferrer, make_train):
        """Defensive check: reason should never be None after a state is set."""
        scenarios = [
            ([], FEED_RECENT, []),                                                     # OPEN
            ([], FEED_RECENT, ["R35"]),                                                # CLOSING_PREDICTED route-only
            ([make_train(phase=TrainPhase.AT_CROSSING)], FEED_RECENT, []),             # CLOSED_INFERRED
            ([make_train(phase=TrainPhase.STRIKE_IN,
                         predicted_at_crossing=NOW + timedelta(seconds=60))],
             FEED_RECENT, []),                                                          # CLOSING_PREDICTED
            ([], NOW - timedelta(seconds=400), []),                                    # STALE_DATA
        ]
        for trains, feed, routes in scenarios:
            inferrer = type(inferrer)(inferrer.config)  # fresh inferrer per scenario
            status = inferrer.update(trains, feed, active_routes=routes)
            assert status.reason is not None, f"reason missing for state {status.state}"
            assert len(status.reason) > 0, f"reason empty for state {status.state}"
