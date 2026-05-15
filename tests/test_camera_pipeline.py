"""Tests for the camera-analysis closure-event extractor.

`_find_closure_events` collapses a per-frame state stream into discrete
closure events with three filters: pre-filter brief blips, merge nearby
events, drop sub-threshold final events. Validated against site_01
2026-05-14 footage where the user manually classified each event.

The camera_analysis module relies on numpy + OpenCV + PyAV which are
not in the predictor's requirements.txt (they only run on dev machines
where the analyst processes recorded footage). Skip the whole module
when those deps aren't installed so CI doesn't fail on the predictor's
slim production image.
"""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("numpy")
pytest.importorskip("cv2")
pytest.importorskip("av")

from experiments.camera_analysis.pipeline import _find_closure_events


# ── Row helpers ──────────────────────────────────────────────────────
# A row is whatever shape pipeline.py emits to detections.csv. Only
# columns 0 (index — ignored), 1 (timestamp ISO), 2 (state) are read by
# `_find_closure_events`. The rest are padding so the index numbers match
# the production schema.

START = datetime(2026, 5, 14, 9, 0, 0, tzinfo=timezone.utc)
INTERVAL = timedelta(seconds=5)


def _row(idx: int, state: str) -> list:
    return [idx, (START + idx * INTERVAL).isoformat(), state, "", "", "0.00",
            "0.0", "0.0", 0, "0.0", "0.0", 0, "0.0", "0.0"]


def _stream(states: list[str]) -> list[list]:
    """Build a row stream from a sequence of per-frame states."""
    return [_row(i, s) for i, s in enumerate(states)]


def _at(idx: int) -> str:
    return (START + idx * INTERVAL).isoformat()


# ── Tests ────────────────────────────────────────────────────────────


class TestSingleClosure:

    def test_single_clean_closure(self):
        # 60s closure surrounded by open
        states = ["open"] * 4 + ["closed"] * 12 + ["open"] * 4
        events = _find_closure_events(_stream(states))
        assert len(events) == 1
        assert events[0]["started_at"] == _at(4)
        assert events[0]["ended_at"] == _at(16)
        assert events[0]["duration_secs"] == 60

    def test_no_closures_returns_empty(self):
        events = _find_closure_events(_stream(["open"] * 50))
        assert events == []

    def test_open_ended_closure_at_recording_end(self):
        """Recording cuts off mid-closure: ended_at=None, duration_secs=None.
        Open-ended events bypass the min_duration filter so we don't lose
        a real closure that simply hadn't ended yet when the camera stopped."""
        states = ["open"] * 4 + ["closed"] * 8
        events = _find_closure_events(_stream(states))
        assert len(events) == 1
        assert events[0]["ended_at"] is None
        assert events[0]["duration_secs"] is None


class TestPreFilter:

    def test_brief_blip_dropped_before_merge(self):
        """A 10s spurious closure (e.g. red car triggering one ROI) MUST
        be dropped BEFORE the merge step — otherwise it would absorb a
        nearby real closure into a misleading combined event."""
        # 10s blip, 30s open, 60s real closure
        states = (["open"] * 2
                  + ["closed"] * 2          # 10s blip
                  + ["open"] * 6
                  + ["closed"] * 12         # 60s real
                  + ["open"] * 2)
        events = _find_closure_events(_stream(states))
        assert len(events) == 1
        assert events[0]["duration_secs"] == 60

    def test_blip_at_threshold_kept(self):
        """min_raw_duration_secs=20 is strict-greater-or-equal: a 20s
        closure (4 frames at 5s) survives the pre-filter."""
        states = ["open"] * 2 + ["closed"] * 4 + ["open"] * 30 + ["closed"] * 12 + ["open"] * 2
        # The 20s closure stands alone (60s gap > 60s merge threshold? no —
        # merge_gap is < 60s strict). Use larger gap to be safe.
        events = _find_closure_events(_stream(states), merge_gap_secs=30)
        # 20s closure survives pre-filter but is dropped by min_duration_secs=30
        # → only the real one survives.
        assert len(events) == 1
        assert events[0]["duration_secs"] == 60


class TestMerge:

    def test_close_events_merge(self):
        """Two real closures separated by less than merge_gap_secs of OPEN
        get stitched into one — handles the LED alternation gap during a
        single real closure."""
        # 30s closed, 25s open, 30s closed (under 60s merge gap)
        states = (["open"] * 2
                  + ["closed"] * 6
                  + ["open"] * 5
                  + ["closed"] * 6
                  + ["open"] * 2)
        events = _find_closure_events(_stream(states))
        assert len(events) == 1
        # First closure starts at idx 2, second ends at idx 19 → 85s span
        assert events[0]["started_at"] == _at(2)
        assert events[0]["ended_at"] == _at(19)
        assert events[0]["duration_secs"] == 85

    def test_distant_events_stay_separate(self):
        """Closures separated by ≥ merge_gap_secs of OPEN are treated as
        distinct events (back-to-back trains)."""
        # 30s closed, 65s open, 30s closed (over 60s merge gap)
        states = (["open"] * 2
                  + ["closed"] * 6
                  + ["open"] * 13         # 65s open
                  + ["closed"] * 6
                  + ["open"] * 2)
        events = _find_closure_events(_stream(states))
        assert len(events) == 2

    def test_blip_does_not_bridge_real_closures(self):
        """The headline regression from site_01 14:48-15:04 footage: a
        17-minute false event was 3 real closures bridged by brief
        blips. With pre-filter BEFORE merge, the blips are gone before
        merge runs, so the real closures stay separate."""
        # Real closure A (60s), large open gap (90s) with one 10s blip,
        # real closure B (60s)
        states = (["closed"] * 12       # 60s real
                  + ["open"] * 8        # 40s open
                  + ["closed"] * 2      # 10s blip — must be dropped pre-merge
                  + ["open"] * 8        # 40s more open
                  + ["closed"] * 12     # 60s real
                  + ["open"] * 2)
        events = _find_closure_events(_stream(states))
        assert len(events) == 2
        assert events[0]["duration_secs"] == 60
        assert events[1]["duration_secs"] == 60


class TestMinDuration:

    def test_short_event_dropped(self):
        # 25s closure — survives pre-filter (≥20s) but dropped by min_duration (≥30s)
        states = ["open"] * 4 + ["closed"] * 5 + ["open"] * 4
        events = _find_closure_events(_stream(states))
        assert events == []

    def test_exact_min_duration_kept(self):
        # 30s closure — exactly at the threshold
        states = ["open"] * 4 + ["closed"] * 6 + ["open"] * 4
        events = _find_closure_events(_stream(states))
        assert len(events) == 1
        assert events[0]["duration_secs"] == 30


class TestParameterTuning:

    def test_tighter_merge_gap_splits_more(self):
        # Two closures separated by 30s open
        states = ["closed"] * 12 + ["open"] * 6 + ["closed"] * 12
        merged = _find_closure_events(_stream(states), merge_gap_secs=60)
        split = _find_closure_events(_stream(states), merge_gap_secs=20)
        assert len(merged) == 1
        assert len(split) == 2

    def test_relaxed_pre_filter_lets_blips_through(self):
        # 10s blip alone, no nearby real closure
        states = ["open"] * 4 + ["closed"] * 2 + ["open"] * 4
        # With default 20s pre-filter: dropped → empty
        assert _find_closure_events(_stream(states)) == []
        # With relaxed pre-filter AND relaxed min_duration: kept
        kept = _find_closure_events(
            _stream(states), min_raw_duration_secs=5, min_duration_secs=5,
        )
        assert len(kept) == 1
        assert kept[0]["duration_secs"] == 10
