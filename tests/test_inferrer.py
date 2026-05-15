"""Tests for CrossingInferrer state derivation logic."""

import copy
from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from src.inferrer import CrossingInferrer
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

    def test_closed_releases_when_only_far_approaching_train_left(self, inferrer, make_train):
        """After last train clears, a far-approaching train (well beyond pre_closure)
        should NOT keep barriers down — there's plenty of time for them to raise
        and re-close before that train arrives. Regression test for the linger
        bug discovered via camera ground truth (see commit message)."""
        at_crossing = make_train(headcode="1A00", phase=TrainPhase.AT_CROSSING)
        inferrer.update([at_crossing], FEED_RECENT)
        assert inferrer.status.state == CrossingState.CLOSED_INFERRED

        # pre_closure_secs in test_config = 120, so 600s is well outside
        far = make_train(
            headcode="2B00",
            phase=TrainPhase.APPROACHING,
            predicted_at_crossing=NOW + timedelta(seconds=600),
        )
        status = inferrer.update([far], FEED_RECENT)
        # Should NOT stay CLOSED — falls through to the OPEN branch since
        # the approaching train is outside pre_closure window
        assert status.state == CrossingState.OPEN

    def test_closed_releases_when_approaching_has_no_eta(self, inferrer, make_train):
        """Approaching train without a predicted_at_crossing time also doesn't
        hold barriers down — we have no signal it's imminent."""
        at_crossing = make_train(headcode="1A00", phase=TrainPhase.AT_CROSSING)
        inferrer.update([at_crossing], FEED_RECENT)
        assert inferrer.status.state == CrossingState.CLOSED_INFERRED

        no_eta = make_train(
            headcode="2B00",
            phase=TrainPhase.APPROACHING,
            predicted_at_crossing=None,
        )
        status = inferrer.update([no_eta], FEED_RECENT)
        assert status.state == CrossingState.OPEN



@freeze_time(NOW)
class TestRoutesIgnored:
    """Routes are unconditionally ignored by the inferrer (see src/inferrer.py
    docstring). Whatever `active_routes` value is supplied — None, [], or any
    list of route names — the inferrer's output (state, confidence, reason,
    predicted_change, predicted_next_state) must be identical to the no-routes
    case. Routes are still observed by RouteMonitor and exposed via the API
    + history for diagnostics; they just cannot influence prediction.
    """

    @pytest.fixture
    def fresh_inferrer(self, test_config):
        """Factory: returns a function that creates a brand-new inferrer.
        Per-variant fresh inferrers prevent state-machine contamination."""
        def _make():
            return CrossingInferrer(copy.deepcopy(test_config))
        return _make

    # ---- Headline regression: routes never change the verdict ----

    @pytest.mark.parametrize("routes", [
        None, [], ["R35"], ["R35", "RA007"], ["R29", "R32", "R34", "R35", "RA007"],
    ])
    def test_routes_only_no_trains_stays_open(self, fresh_inferrer, routes):
        """Headline regression: routes SET, no trains → must stay OPEN.
        Previously this was the route-only CLOSING_PREDICTED branch."""
        status = fresh_inferrer().update([], FEED_RECENT, active_routes=routes)
        assert status.state == CrossingState.OPEN
        assert status.confidence == 0.8

    @pytest.mark.parametrize("routes", [None, [], ["R35"], ["R35", "RA007"]])
    def test_at_crossing_confidence_unchanged_by_routes(self, fresh_inferrer, make_train, routes):
        """AT_CROSSING confidence is the no-routes value regardless."""
        train = make_train(phase=TrainPhase.AT_CROSSING)
        status = fresh_inferrer().update([train], FEED_RECENT, active_routes=routes)
        assert status.state == CrossingState.CLOSED_INFERRED
        assert status.confidence == 0.9

    @pytest.mark.parametrize("routes", [None, [], ["R32"], ["R29", "R32"]])
    def test_strike_in_with_routes_stays_closing_predicted(self, fresh_inferrer, make_train, routes):
        """STRIKE_IN: routes can NOT promote to CLOSED_INFERRED."""
        train = make_train(phase=TrainPhase.STRIKE_IN,
                           predicted_at_crossing=NOW + timedelta(seconds=60))
        status = fresh_inferrer().update([train], FEED_RECENT, active_routes=routes)
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert status.confidence == 0.8

    @pytest.mark.parametrize("routes", [None, [], ["RA007"], ["R29", "RA007"]])
    def test_far_approaching_train_stays_open(self, fresh_inferrer, make_train, routes):
        """APPROACHING far away: routes do not pull state to CLOSING_PREDICTED.
        Falls back to pre_closure_secs distance check (300s > 120s → OPEN)."""
        train = make_train(phase=TrainPhase.APPROACHING,
                           predicted_at_crossing=NOW + timedelta(seconds=300))
        status = fresh_inferrer().update([train], FEED_RECENT, active_routes=routes)
        assert status.state == CrossingState.OPEN

    def test_long_route_hold_does_not_downgrade_to_unknown(self, fresh_inferrer):
        """The route-hold cap path is gone — even after many minutes of
        routes-set state, we stay OPEN (not UNKNOWN)."""
        with freeze_time(NOW) as frozen:
            inf = fresh_inferrer()
            inf.update([], datetime.now(timezone.utc), active_routes=["R35"])
            frozen.tick(delta=timedelta(seconds=2000))
            status = inf.update([], datetime.now(timezone.utc), active_routes=["R35"])
            assert status.state == CrossingState.OPEN

    # ---- Reason strings stay route-free ----

    @pytest.mark.parametrize("routes", [["R35"], ["R35", "RA007"], ["R29", "R32"]])
    def test_reason_never_mentions_routes(self, fresh_inferrer, make_train, routes):
        """Reason strings must not name routes when routes are present —
        prevents misleading "route SET" wording when the inferrer ignored them."""
        scenarios = [
            ([], "no trains"),
            ([make_train(phase=TrainPhase.AT_CROSSING)], "at crossing"),
            ([make_train(phase=TrainPhase.STRIKE_IN,
                         predicted_at_crossing=NOW + timedelta(seconds=60))], None),
            ([make_train(phase=TrainPhase.APPROACHING,
                         predicted_at_crossing=NOW + timedelta(seconds=60))], None),
        ]
        for trains, _ in scenarios:
            inf = fresh_inferrer()
            status = inf.update(trains, FEED_RECENT, active_routes=routes)
            assert "route" not in status.reason.lower()
            for r in routes:
                assert r not in status.reason

    # ---- Strict identity check across all major scenarios ----

    @pytest.mark.parametrize("routes", [
        None, [], ["R35"], ["R35", "RA007"], ["R29", "R32", "R34"],
    ])
    def test_full_output_identical_regardless_of_routes(
        self, fresh_inferrer, make_train, routes
    ):
        """The strongest possible guarantee: for every scenario, the inferrer's
        full output (state, confidence, reason, predicted_change,
        predicted_next_state) is bit-for-bit identical to the routes=[] case.

        Any future drift in the inferrer that reintroduces route influence
        will fail this test. Each variant uses a freshly-constructed inferrer
        so prior-state contamination cannot mask a leak.
        """
        scenarios = [
            ("no trains", []),
            ("at crossing", [make_train(phase=TrainPhase.AT_CROSSING)]),
            ("strike-in",
             [make_train(phase=TrainPhase.STRIKE_IN,
                         predicted_at_crossing=NOW + timedelta(seconds=60))]),
            ("at-station",
             [make_train(phase=TrainPhase.AT_STATION,
                         predicted_at_crossing=NOW + timedelta(seconds=90))]),
            ("approaching close",
             [make_train(phase=TrainPhase.APPROACHING,
                         predicted_at_crossing=NOW + timedelta(seconds=60))]),
            ("approaching far",
             [make_train(phase=TrainPhase.APPROACHING,
                         predicted_at_crossing=NOW + timedelta(seconds=300))]),
            ("two trains",
             [make_train(headcode="1A23", phase=TrainPhase.AT_CROSSING),
              make_train(headcode="2B45", phase=TrainPhase.APPROACHING,
                         predicted_at_crossing=NOW + timedelta(seconds=200))]),
        ]
        for label, trains in scenarios:
            baseline_inf = fresh_inferrer()
            baseline = baseline_inf.update(trains, FEED_RECENT, active_routes=[])
            baseline_snapshot = (baseline.state, baseline.confidence, baseline.reason,
                                 baseline.predicted_change, baseline.predicted_next_state)

            variant_inf = fresh_inferrer()
            variant = variant_inf.update(trains, FEED_RECENT, active_routes=routes)
            variant_snapshot = (variant.state, variant.confidence, variant.reason,
                                variant.predicted_change, variant.predicted_next_state)

            assert baseline_snapshot == variant_snapshot, (
                f"Routes leaked into prediction for scenario {label!r} with "
                f"routes={routes!r}\n  baseline: {baseline_snapshot}\n  "
                f"variant:  {variant_snapshot}")

    # ---- Stateful identity check (transitions) ----

    @pytest.mark.parametrize("routes", [None, [], ["R35"], ["R35", "RA007"]])
    def test_post_clearance_window_unchanged_by_routes(
        self, fresh_inferrer, make_train, routes
    ):
        """OPENING_PREDICTED → OPEN flow happens identically whether routes
        are present during the clearance window or not."""
        with freeze_time(NOW) as frozen:
            inf = fresh_inferrer()
            train = make_train(phase=TrainPhase.AT_CROSSING)
            inf.update([train], datetime.now(timezone.utc), active_routes=routes)
            assert inf.status.state == CrossingState.CLOSED_INFERRED

            # All trains gone, routes still present (or absent — tested both)
            status = inf.update([], datetime.now(timezone.utc), active_routes=routes)
            assert status.state == CrossingState.OPENING_PREDICTED

            # After post_clearance window, routes still present → OPEN
            frozen.tick(delta=timedelta(seconds=10))
            status = inf.update([], datetime.now(timezone.utc), active_routes=routes)
            assert status.state == CrossingState.OPEN


@freeze_time(NOW)
class TestStateReason:
    """Every state transition records a human-readable reason. These tests
    verify that meaningful reason strings are populated on every code path.
    They check for substrings rather than exact text so wording can be
    tweaked without breaking tests.
    """

    def test_open_no_trains_reason(self, inferrer):
        status = inferrer.update([], FEED_RECENT)
        assert status.state == CrossingState.OPEN
        assert status.reason is not None
        assert "no trains" in status.reason.lower()

    def test_at_crossing_reason_includes_headcode(self, inferrer, make_train):
        train = make_train(headcode="1A23", phase=TrainPhase.AT_CROSSING)
        status = inferrer.update([train], FEED_RECENT)
        assert status.state == CrossingState.CLOSED_INFERRED
        assert "1A23" in status.reason
        assert "at crossing" in status.reason.lower()

    def test_strike_in_reason_includes_headcode(self, inferrer, make_train):
        train = make_train(headcode="2B45", phase=TrainPhase.STRIKE_IN,
                           predicted_at_crossing=NOW + timedelta(seconds=60))
        status = inferrer.update([train], FEED_RECENT)
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert "2B45" in status.reason
        assert "strike-in" in status.reason.lower()

    def test_approaching_within_pre_closure_reason(self, inferrer, make_train):
        train = make_train(headcode="1A23", phase=TrainPhase.APPROACHING,
                           predicted_at_crossing=NOW + timedelta(seconds=60))
        status = inferrer.update([train], FEED_RECENT)
        assert status.state == CrossingState.CLOSING_PREDICTED
        assert "1A23" in status.reason
        assert "pre_closure" in status.reason.lower() or "60s" in status.reason

    def test_approaching_distant_reason(self, inferrer, make_train):
        train = make_train(headcode="1A23", phase=TrainPhase.APPROACHING,
                           predicted_at_crossing=NOW + timedelta(seconds=600))
        status = inferrer.update([train], FEED_RECENT)
        assert status.state == CrossingState.OPEN
        assert "1A23" in status.reason
        assert "outside" in status.reason.lower()

    def test_stale_data_reason(self, inferrer):
        very_old_feed = NOW - timedelta(seconds=400)
        status = inferrer.update([], very_old_feed)
        assert status.state == CrossingState.STALE_DATA
        assert "feed" in status.reason.lower()

    def test_opening_predicted_reason(self, inferrer, make_train):
        # Train at crossing, then clears
        train = make_train(phase=TrainPhase.AT_CROSSING)
        inferrer.update([train], FEED_RECENT)
        status = inferrer.update([], FEED_RECENT)
        assert status.state == CrossingState.OPENING_PREDICTED
        assert "post-clearance" in status.reason.lower() or "cctv" in status.reason.lower()

    def test_was_closed_reason_mentions_held(self, inferrer, make_train):
        # Get into CLOSED_INFERRED first
        train = make_train(headcode="1A23", phase=TrainPhase.AT_CROSSING)
        inferrer.update([train], FEED_RECENT)
        # Now follow with a strike-in train (consecutive trains): was_closed branch
        next_train = make_train(headcode="2B45", phase=TrainPhase.STRIKE_IN,
                                predicted_at_crossing=NOW + timedelta(seconds=60))
        status = inferrer.update([next_train], FEED_RECENT)
        assert status.state == CrossingState.CLOSED_INFERRED
        assert "held closed" in status.reason.lower() or "still active" in status.reason.lower()

    def test_reason_is_set_on_every_state(self, inferrer, make_train):
        """Defensive check: reason should never be None after a state is set."""
        scenarios = [
            ([], FEED_RECENT),                                                          # OPEN
            ([make_train(phase=TrainPhase.AT_CROSSING)], FEED_RECENT),                  # CLOSED_INFERRED
            ([make_train(phase=TrainPhase.STRIKE_IN,
                         predicted_at_crossing=NOW + timedelta(seconds=60))],
             FEED_RECENT),                                                              # CLOSING_PREDICTED
            ([], NOW - timedelta(seconds=400)),                                         # STALE_DATA
        ]
        for trains, feed in scenarios:
            inferrer = type(inferrer)(inferrer.config)  # fresh inferrer per scenario
            status = inferrer.update(trains, feed)
            assert status.reason is not None, f"reason missing for state {status.state}"
            assert len(status.reason) > 0, f"reason empty for state {status.state}"
