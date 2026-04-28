"""Tests for TrainTracker — TD step/cancel, TRUST movements, RTT updates, classify, cleanup."""

import pytest
from datetime import datetime, timedelta, timezone

from freezegun import freeze_time

from src.models import TrackedTrain, TrainPhase, Direction


# ─── handle_td_step ──────────────────────────────────────────────────────────

class TestHandleTdStep:
    def test_new_train_created(self, tracker, now):
        tracker.handle_td_step("", "A027", "1A23", now)
        assert "1A23" in tracker.trains
        t = tracker.trains["1A23"]
        assert t.headcode == "1A23"
        assert t.last_berth == "A027"

    def test_phase_progresses_up(self, tracker, now):
        tracker.handle_td_step("", "A027", "1A23", now)
        assert tracker.trains["1A23"].phase == TrainPhase.APPROACHING

        tracker.handle_td_step("A027", "0040", "1A23", now + timedelta(seconds=10))
        assert tracker.trains["1A23"].phase == TrainPhase.STRIKE_IN

        tracker.handle_td_step("0040", "0036", "1A23", now + timedelta(seconds=20))
        assert tracker.trains["1A23"].phase == TrainPhase.AT_CROSSING

        tracker.handle_td_step("0036", "0034", "1A23", now + timedelta(seconds=30))
        assert tracker.trains["1A23"].phase == TrainPhase.CLEARED

    def test_phase_progresses_down(self, tracker, now):
        tracker.handle_td_step("", "0033", "2B45", now)
        assert tracker.trains["2B45"].phase == TrainPhase.APPROACHING

        tracker.handle_td_step("0033", "0035", "2B45", now + timedelta(seconds=10))
        assert tracker.trains["2B45"].phase == TrainPhase.STRIKE_IN

        tracker.handle_td_step("0035", "0041", "2B45", now + timedelta(seconds=20))
        assert tracker.trains["2B45"].phase == TrainPhase.AT_CROSSING

        tracker.handle_td_step("0041", "A027", "2B45", now + timedelta(seconds=30))
        assert tracker.trains["2B45"].phase == TrainPhase.CLEARED

    def test_direction_inferred_from_berth(self, tracker, now):
        tracker.handle_td_step("", "A027", "1A23", now)
        assert tracker.trains["1A23"].direction == Direction.UP

        tracker.handle_td_step("", "0033", "2B45", now)
        assert tracker.trains["2B45"].direction == Direction.DOWN

    def test_phase_regression_prevented(self, tracker, now):
        tracker.handle_td_step("", "0036", "1A23", now)
        assert tracker.trains["1A23"].phase == TrainPhase.AT_CROSSING

        tracker.handle_td_step("0036", "A027", "1A23", now + timedelta(seconds=10))
        # A027 is up/approach — that would be a regression from AT_CROSSING
        # But also A027 is down/clear which would be a progression
        # The preferred direction is UP (already set), so it resolves to APPROACHING/UP
        # which is blocked as regression — phase stays AT_CROSSING
        assert tracker.trains["1A23"].phase == TrainPhase.AT_CROSSING

    def test_blank_headcode_ignored(self, tracker, now):
        tracker.handle_td_step("", "A027", "", now)
        assert len(tracker.trains) == 0

        tracker.handle_td_step("", "A027", "  ", now)
        assert len(tracker.trains) == 0

    def test_irrelevant_berths_ignored(self, tracker, now):
        tracker.handle_td_step("", "ZZZZ", "1A23", now)
        assert len(tracker.trains) == 0

    def test_station_berth_entry_sets_sub_position(self, tracker, now):
        tracker.handle_td_step("", "0038", "1A23", now)
        t = tracker.trains["1A23"]
        assert t.sub_position == "entry"
        assert t.station == "Angmering"

    def test_leaving_station_berth_clears_sub_position(self, tracker, now):
        tracker.handle_td_step("", "0038", "1A23", now)
        assert tracker.trains["1A23"].sub_position == "entry"

        tracker.handle_td_step("0038", "0036", "1A23", now + timedelta(seconds=10))
        assert tracker.trains["1A23"].sub_position is None


# ─── handle_td_cancel ────────────────────────────────────────────────────────

class TestHandleTdCancel:
    def test_train_marked_lost(self, tracker, now):
        tracker.handle_td_step("", "A027", "1A23", now)
        tracker.handle_td_cancel("A027", "1A23", now + timedelta(seconds=5))
        assert tracker.trains["1A23"].phase == TrainPhase.LOST

    def test_no_effect_if_berth_mismatch(self, tracker, now):
        tracker.handle_td_step("", "A027", "1A23", now)
        tracker.handle_td_cancel("0036", "1A23", now + timedelta(seconds=5))
        assert tracker.trains["1A23"].phase == TrainPhase.APPROACHING

    def test_no_effect_for_unknown_headcode(self, tracker, now):
        tracker.handle_td_cancel("A027", "XXXX", now)
        assert "XXXX" not in tracker.trains

    def test_irrelevant_berths_ignored(self, tracker, now):
        tracker.handle_td_step("", "A027", "1A23", now)
        tracker.handle_td_cancel("ZZZZ", "1A23", now + timedelta(seconds=5))
        assert tracker.trains["1A23"].phase == TrainPhase.APPROACHING


# ─── handle_trust_movement ───────────────────────────────────────────────────

class TestHandleTrustMovement:
    def test_creates_new_train(self, tracker, now):
        tracker.handle_trust_movement("AB1A23CD00", "87998", "DEPARTURE", now, headcode="1A23")
        assert "1A23" in tracker.trains
        t = tracker.trains["1A23"]
        assert t.direction == Direction.UP
        assert t.train_id == "AB1A23CD00"

    def test_predict_action_sets_predicted_at_crossing(self, tracker, now):
        tracker.handle_trust_movement("AB1A23CD00", "87998", "DEPARTURE", now, headcode="1A23")
        t = tracker.trains["1A23"]
        assert t.predicted_at_crossing == now + timedelta(seconds=90)
        assert t.direction == Direction.UP

    def test_predict_action_down(self, tracker, now):
        tracker.handle_trust_movement("AB2B45CD00", "87997", "DEPARTURE", now, headcode="2B45")
        t = tracker.trains["2B45"]
        assert t.predicted_at_crossing == now + timedelta(seconds=120)
        assert t.direction == Direction.DOWN

    def test_clear_action_sets_cleared(self, tracker, now):
        # Pre-seed a down train
        tracker.handle_td_step("", "0033", "2B45", now)
        tracker.handle_trust_movement("AB2B45CD00", "87998", "ARRIVAL", now + timedelta(seconds=30), headcode="2B45")
        assert tracker.trains["2B45"].phase == TrainPhase.CLEARED

    def test_at_station_sets_direction_and_station(self, tracker, now):
        tracker.handle_td_step("", "A027", "1A23", now)
        tracker.handle_trust_movement("AB1A23CD00", "87998", "ARRIVAL", now + timedelta(seconds=10), headcode="1A23")
        t = tracker.trains["1A23"]
        assert t.direction == Direction.UP
        assert t.station == "Angmering"

    def test_ignores_unknown_stanox(self, tracker, now):
        tracker.handle_trust_movement("AB1A23CD00", "99999", "DEPARTURE", now, headcode="1A23")
        assert "1A23" not in tracker.trains

    def test_headcode_extracted_from_train_id(self, tracker, now):
        tracker.handle_trust_movement("AB1A23CD00", "87998", "DEPARTURE", now)
        assert "1A23" in tracker.trains


# ─── handle_rtt_update ───────────────────────────────────────────────────────

class TestHandleRttUpdate:
    def test_at_platform_sets_sub_position(self, tracker, now):
        tracker.handle_td_step("", "0038", "1A23", now)
        with freeze_time(now + timedelta(seconds=5)):
            tracker.handle_rtt_update("1A23", "Angmering", "1", "AT_PLATFORM")
        t = tracker.trains["1A23"]
        assert t.sub_position == "at_platform"
        assert t.phase == TrainPhase.AT_STATION

    def test_at_platform_goring_up_clears(self, tracker, now):
        tracker.handle_td_step("", "0040", "1A23", now)
        tracker.trains["1A23"].direction = Direction.UP
        with freeze_time(now + timedelta(seconds=5)):
            tracker.handle_rtt_update("1A23", "Goring-by-Sea", "1", "AT_PLATFORM")
        assert tracker.trains["1A23"].phase == TrainPhase.CLEARED

    def test_at_platform_angmering_down_clears(self, tracker, now):
        tracker.handle_td_step("", "0041", "2B45", now)
        tracker.trains["2B45"].direction = Direction.DOWN
        with freeze_time(now + timedelta(seconds=5)):
            tracker.handle_rtt_update("2B45", "Angmering", "2", "AT_PLATFORM")
        assert tracker.trains["2B45"].phase == TrainPhase.CLEARED

    def test_ignores_already_cleared(self, tracker, now):
        tracker.handle_td_step("", "0034", "1A23", now)
        assert tracker.trains["1A23"].phase == TrainPhase.CLEARED
        with freeze_time(now + timedelta(seconds=5)):
            tracker.handle_rtt_update("1A23", "Angmering", "1", "AT_PLATFORM")
        assert tracker.trains["1A23"].phase == TrainPhase.CLEARED

    def test_ignores_stale_trains(self, tracker, now):
        tracker.handle_td_step("", "0038", "1A23", now)
        with freeze_time(now + timedelta(seconds=700)):
            tracker.handle_rtt_update("1A23", "Angmering", "1", "AT_PLATFORM")
        # sub_position should NOT be updated (stale check: age > 600s)
        assert tracker.trains["1A23"].sub_position == "entry"

    def test_ignores_unknown_headcode(self, tracker, now):
        with freeze_time(now):
            tracker.handle_rtt_update("XXXX", "Angmering", "1", "AT_PLATFORM")
        assert "XXXX" not in tracker.trains


# ─── _classify_berth ─────────────────────────────────────────────────────────

class TestClassifyBerth:
    def test_shared_berth_up_preferred(self, tracker):
        phase, direction = tracker._classify_berth("A027", preferred_direction=Direction.UP)
        assert phase == TrainPhase.APPROACHING
        assert direction == Direction.UP

    def test_shared_berth_down_preferred(self, tracker):
        phase, direction = tracker._classify_berth("A027", preferred_direction=Direction.DOWN)
        assert phase == TrainPhase.CLEARED
        assert direction == Direction.DOWN

    def test_approach_up(self, tracker):
        phase, direction = tracker._classify_berth("A027")
        assert phase is not None
        assert direction is not None

    def test_strike_in_up(self, tracker):
        phase, direction = tracker._classify_berth("0040")
        assert phase == TrainPhase.STRIKE_IN
        assert direction == Direction.UP

    def test_at_crossing_up(self, tracker):
        phase, direction = tracker._classify_berth("0036")
        assert phase == TrainPhase.AT_CROSSING
        assert direction == Direction.UP

    def test_clear_up(self, tracker):
        phase, direction = tracker._classify_berth("0034")
        assert phase == TrainPhase.CLEARED
        assert direction == Direction.UP

    def test_approach_down(self, tracker):
        phase, direction = tracker._classify_berth("0033")
        assert phase == TrainPhase.APPROACHING
        assert direction == Direction.DOWN

    def test_strike_in_down(self, tracker):
        phase, direction = tracker._classify_berth("0037")
        assert phase == TrainPhase.STRIKE_IN
        assert direction == Direction.DOWN

    def test_at_crossing_down(self, tracker):
        phase, direction = tracker._classify_berth("0041")
        assert phase == TrainPhase.AT_CROSSING
        assert direction == Direction.DOWN

    def test_unknown_berth(self, tracker):
        phase, direction = tracker._classify_berth("ZZZZ")
        assert phase is None
        assert direction is None


# ─── get_active_trains / cleanup ─────────────────────────────────────────────

class TestGetActiveTrainsAndCleanup:
    def test_filters_out_cleared_and_lost(self, tracker, now):
        tracker.handle_td_step("", "A027", "1A23", now)
        tracker.handle_td_step("", "0034", "2B45", now)  # cleared
        with freeze_time(now + timedelta(seconds=1)):
            active = tracker.get_active_trains()
        headcodes = [t.headcode for t in active]
        assert "1A23" in headcodes
        assert "2B45" not in headcodes

    def test_stale_trains_marked_lost(self, tracker, now):
        tracker.handle_td_step("", "A027", "1A23", now)
        with freeze_time(now + timedelta(seconds=130)):
            active = tracker.get_active_trains()
        assert len(active) == 0
        assert tracker.trains["1A23"].phase == TrainPhase.LOST

    def test_old_cleared_trains_removed(self, tracker, now):
        tracker.handle_td_step("", "0034", "1A23", now)
        assert tracker.trains["1A23"].phase == TrainPhase.CLEARED
        with freeze_time(now + timedelta(seconds=200)):
            tracker.get_active_trains()
        assert "1A23" not in tracker.trains

    def test_old_lost_trains_removed(self, tracker, now):
        tracker.handle_td_step("", "A027", "1A23", now)
        tracker.handle_td_cancel("A027", "1A23", now)
        assert tracker.trains["1A23"].phase == TrainPhase.LOST
        with freeze_time(now + timedelta(seconds=200)):
            tracker.get_active_trains()
        assert "1A23" not in tracker.trains
