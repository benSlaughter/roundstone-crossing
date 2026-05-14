"""Tests for the camera-analysis detector.

The detector's headline guarantee — proven across 34 manually-validated
frames from site_01_2026-05-14 — is that wig-wag housings have RED
REFLECTIVE PANELS around the LEDs which contaminate naive red-pixel
detection. The fix is twofold:

  1. Discriminate LEDs from panels by brightness (V≥220 + S≥180).
  2. Require BOTH housings to show ≥2 lit pixels before calling ON.

These tests synthesise small BGR frames with known pixel content and
verify the detector's response to each scenario.
"""

import numpy as np
import pytest

from experiments.camera_analysis.detect import (
    State,
    WIG_WAG_LED_MIN_PIXELS,
    detect_frame,
    measure_roi,
)


# Three ROIs covering distinct regions of a 100x300 synthetic frame.
ROIS = {
    "wig_wag_left":  (0,  0, 30, 30),
    "wig_wag_right": (50, 0, 30, 30),
    "barrier_arm":   (0, 50, 100, 30),
}


def _frame() -> np.ndarray:
    """Black 100×300 BGR canvas."""
    return np.zeros((100, 300, 3), dtype=np.uint8)


def _paint(frame: np.ndarray, x: int, y: int, w: int, h: int, bgr: tuple) -> None:
    frame[y:y + h, x:x + w] = bgr


# Bright red (lit LED) saturates V; dim red (passive panel) does not.
LED_BGR = (0, 0, 255)            # V=255, S=255 → matches LED mask
PANEL_BGR = (0, 0, 140)          # V=140, S=255 → matches loose red, NOT LED mask


class TestMeasureRoi:

    def test_empty_roi_returns_zeros(self):
        frame = _frame()
        m = measure_roi(frame, (0, 0, 0, 0))
        assert m.red_pixel_ratio == 0.0
        assert m.led_pixel_count == 0

    def test_all_led_pixels_counted(self):
        frame = _frame()
        _paint(frame, 0, 0, 10, 10, LED_BGR)
        m = measure_roi(frame, (0, 0, 10, 10))
        assert m.led_pixel_count == 100

    def test_panel_pixels_excluded_from_led_count(self):
        """Reflective panels register on the loose red mask but NOT on
        the strict LED mask — this is the panel-discrimination guarantee."""
        frame = _frame()
        _paint(frame, 0, 0, 10, 10, PANEL_BGR)
        m = measure_roi(frame, (0, 0, 10, 10))
        assert m.red_pixel_ratio > 0     # loose mask catches the panel
        assert m.led_pixel_count == 0    # strict LED mask rejects it


class TestDetectFrame:

    def test_both_housings_lit_calls_closed(self):
        """Headline path: LEDs in both wig-wag ROIs → CLOSED."""
        frame = _frame()
        _paint(frame, 0, 0, 5, 5, LED_BGR)     # left housing: 25 LED pixels
        _paint(frame, 50, 0, 5, 5, LED_BGR)    # right housing: 25 LED pixels
        d = detect_frame(frame, ROIS)
        assert d.wig_wag_state == State.ON
        assert d.crossing_state == State.CLOSED

    def test_only_one_housing_lit_stays_open(self):
        """Single-housing artefact (sun glint, parked car) must not trigger
        a closure call. This is the regression that was broken in detector
        v1 and identified by the user during manual validation."""
        frame = _frame()
        _paint(frame, 0, 0, 5, 5, LED_BGR)     # left housing only
        d = detect_frame(frame, ROIS)
        assert d.wig_wag_state == State.OFF
        assert d.crossing_state == State.OPEN

    def test_panels_in_both_housings_stay_open(self):
        """Reflective panels (the V<200 baseline red) in both housings
        must not be confused with lit LEDs. This is the headline panel-
        discrimination property identified during validation."""
        frame = _frame()
        _paint(frame, 0, 0, 30, 30, PANEL_BGR)    # left ROI fully panel-red
        _paint(frame, 50, 0, 30, 30, PANEL_BGR)   # right ROI fully panel-red
        d = detect_frame(frame, ROIS)
        assert d.wig_wag_left.red_pixel_ratio > 0.5
        assert d.wig_wag_right.red_pixel_ratio > 0.5
        assert d.wig_wag_left.led_pixel_count == 0
        assert d.wig_wag_right.led_pixel_count == 0
        assert d.crossing_state == State.OPEN

    def test_below_min_pixels_in_one_housing_stays_open(self):
        """Boundary check: WIG_WAG_LED_MIN_PIXELS-1 lit pixels in one
        housing isn't enough — the BOTH-housings rule must hold strictly."""
        frame = _frame()
        # Right housing: well above threshold
        _paint(frame, 50, 0, 5, 5, LED_BGR)
        # Left housing: only WIG_WAG_LED_MIN_PIXELS - 1 lit pixels
        deficit = WIG_WAG_LED_MIN_PIXELS - 1
        for i in range(deficit):
            _paint(frame, i, 0, 1, 1, LED_BGR)
        d = detect_frame(frame, ROIS)
        assert d.wig_wag_left.led_pixel_count == deficit
        assert d.crossing_state == State.OPEN

    def test_at_min_pixels_in_both_housings_calls_closed(self):
        """Boundary check the other way: exactly WIG_WAG_LED_MIN_PIXELS
        in each housing IS enough."""
        frame = _frame()
        for i in range(WIG_WAG_LED_MIN_PIXELS):
            _paint(frame, i, 0, 1, 1, LED_BGR)
            _paint(frame, 50 + i, 0, 1, 1, LED_BGR)
        d = detect_frame(frame, ROIS)
        assert d.wig_wag_left.led_pixel_count == WIG_WAG_LED_MIN_PIXELS
        assert d.wig_wag_right.led_pixel_count == WIG_WAG_LED_MIN_PIXELS
        assert d.crossing_state == State.CLOSED

    def test_high_confidence_when_wig_wag_and_barrier_agree(self):
        frame = _frame()
        _paint(frame, 0, 0, 5, 5, LED_BGR)
        _paint(frame, 50, 0, 5, 5, LED_BGR)
        _paint(frame, 0, 50, 100, 30, LED_BGR)   # barrier ROI also red
        d = detect_frame(frame, ROIS)
        assert d.crossing_state == State.CLOSED
        assert d.barrier_state == State.DOWN
        assert d.confidence == 0.95

    def test_low_confidence_when_wig_wag_on_but_barrier_appears_up(self):
        """Real footage scenario: wig-wags lit but a tall vehicle hides
        the barrier. Crossing call stays CLOSED, confidence drops."""
        frame = _frame()
        _paint(frame, 0, 0, 5, 5, LED_BGR)
        _paint(frame, 50, 0, 5, 5, LED_BGR)
        # No paint in barrier ROI
        d = detect_frame(frame, ROIS)
        assert d.crossing_state == State.CLOSED
        assert d.barrier_state == State.UP
        assert d.confidence == 0.85

    def test_orange_vehicle_in_barrier_roi_does_not_pull_state_closed(self):
        """The barrier ROI can false-positive on bright orange vehicles
        (the HSV red mask catches orange). Wig-wag rule is primary —
        crossing state must stay OPEN despite the barrier-down confusion."""
        frame = _frame()
        # Bright orange: high V, high S, hue ~10–15 — within loose red mask
        orange = (0, 100, 255)
        _paint(frame, 0, 50, 100, 30, orange)
        d = detect_frame(frame, ROIS)
        assert d.barrier_state == State.DOWN  # detector says barrier looks down
        assert d.crossing_state == State.OPEN  # but wig-wag rule overrides
