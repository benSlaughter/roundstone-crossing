"""Per-frame barrier and wig-wag state detection.

Wig-wag detection relies on the fact that the housings have **bright LEDs**
that, when lit, saturate to near-pure white-bright red (HSV V≥220, S≥180).
The reflective panels around the LEDs are also red but much dimmer
(V≈100–180), so a brightness threshold reliably separates "LED is on"
from "passive reflective material in frame".

Both wig-wag housings flash during a closure (alternating, but the camera
exposure usually catches enough to register pixels in both), so we
require pixel counts in BOTH housings to confirm a real flash. Sun
glints and other small bright-red artefacts typically only appear in one
housing and are rejected.

The barrier arm is recorded as a confirmation signal but does not
override the wig-wag call (it can be hidden by tall vehicles or, in the
opposite direction, falsely triggered by orange vehicles passing
through).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np


class State(Enum):
    UNKNOWN = "unknown"
    OFF = "off"
    ON = "on"
    UP = "up"
    DOWN = "down"
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class RoiMetric:
    """Metrics computed for a single ROI on one frame."""
    red_pixel_ratio: float       # fraction matching the loose "saturated red" mask
    led_pixel_count: int         # absolute count of "lit LED" pixels (V≥220, S≥180, red hue)
    mean_red: float
    mean_green: float
    mean_blue: float

    @property
    def redness(self) -> float:
        """Excess red over the mean of green+blue, scaled to 0..1."""
        gb = (self.mean_green + self.mean_blue) / 2.0 + 1e-6
        return max(0.0, (self.mean_red - gb) / 255.0)


@dataclass
class FrameDetection:
    """Detection result for one frame."""
    wig_wag_left: RoiMetric
    wig_wag_right: RoiMetric
    barrier_arm: RoiMetric

    wig_wag_state: State          # ON / OFF
    barrier_state: State          # UP / DOWN — confirmation only
    crossing_state: State         # OPEN / CLOSED — primary call
    confidence: float


# Wig-wag LED detection thresholds — tuned on the 2026-05-14 site_01
# footage with manually-validated frames.
#
# Validation showed the housings carry RED REFLECTIVE PANELS around the
# LEDs that register as ~3–6% saturated red even when the wig-wag is
# off. The panels are dim (V<200), the LEDs when lit saturate to V≈255.
# So we count "lit LED" pixels using V≥220 + S≥180 (vs the looser
# V≥100 + S≥120 used to score the panel).
WIG_WAG_LED_V_MIN = 220
WIG_WAG_LED_S_MIN = 180
# Per-housing minimum lit pixels to call that housing "on". Real
# closures show ≥2 lit pixels in each housing in every validated frame;
# false positives caused by sun glints / panels typically affect only
# one housing (the other stays at 0 lit pixels).
WIG_WAG_LED_MIN_PIXELS = 2

# Barrier arm thresholds (loose red mask). Used as confirmation only.
BARRIER_RED_RATIO_DOWN = 0.02
BARRIER_REDNESS_DOWN = 0.03


def _crop(frame_bgr: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = roi
    H, W = frame_bgr.shape[:2]
    return frame_bgr[max(0, y):min(H, y + h), max(0, x):min(W, x + w)]


def _saturated_red_mask(bgr: np.ndarray) -> np.ndarray:
    """Loose red-pixel mask (catches LEDs and reflective panels)."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lower1 = cv2.inRange(hsv, (0, 120, 100), (10, 255, 255))
    lower2 = cv2.inRange(hsv, (170, 120, 100), (180, 255, 255))
    return (lower1 | lower2) > 0


def _led_mask(bgr: np.ndarray) -> np.ndarray:
    """Strict mask for *lit LED* pixels.

    Requires near-saturated brightness (V≥220) on top of strong red
    saturation (S≥180). Reflective panels are dim red and excluded; sun
    glints with the wrong hue are excluded.
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lower1 = cv2.inRange(hsv,
                         (0, WIG_WAG_LED_S_MIN, WIG_WAG_LED_V_MIN),
                         (10, 255, 255))
    lower2 = cv2.inRange(hsv,
                         (170, WIG_WAG_LED_S_MIN, WIG_WAG_LED_V_MIN),
                         (180, 255, 255))
    return (lower1 | lower2) > 0


def measure_roi(frame_bgr: np.ndarray, roi: tuple[int, int, int, int]) -> RoiMetric:
    """Compute red-saturation metrics for a single ROI."""
    crop = _crop(frame_bgr, roi)
    if crop.size == 0:
        return RoiMetric(0.0, 0, 0.0, 0.0, 0.0)
    red_mask = _saturated_red_mask(crop)
    led_mask = _led_mask(crop)
    red_ratio = float(red_mask.sum()) / red_mask.size
    led_count = int(led_mask.sum())
    mean_b, mean_g, mean_r = (float(c) for c in crop.reshape(-1, 3).mean(axis=0))
    return RoiMetric(red_pixel_ratio=red_ratio,
                     led_pixel_count=led_count,
                     mean_red=mean_r, mean_green=mean_g, mean_blue=mean_b)


def detect_frame(frame_bgr: np.ndarray, rois: dict) -> FrameDetection:
    """Run all detectors against a single frame.

    Wig-wag call:
      - Each housing is "on" if it contains ≥WIG_WAG_LED_MIN_PIXELS
        bright-saturated red pixels (the LED).
      - Wig-wag is ON only if BOTH housings register a lit LED. This
        rejects single-housing artefacts (sun glints, parked vehicles
        showing through one ROI) which are the dominant false-positive
        source on this site.

    Barrier arm:
      - Loose red-pixel ratio test (panel + arm both contribute).
      - Recorded as confirmation only — does not override wig-wag.
    """
    wl = measure_roi(frame_bgr, rois["wig_wag_left"])
    wr = measure_roi(frame_bgr, rois["wig_wag_right"])
    ba = measure_roi(frame_bgr, rois["barrier_arm"])

    left_on = wl.led_pixel_count >= WIG_WAG_LED_MIN_PIXELS
    right_on = wr.led_pixel_count >= WIG_WAG_LED_MIN_PIXELS
    wig_wag_state = State.ON if (left_on and right_on) else State.OFF

    barrier_down = (ba.red_pixel_ratio >= BARRIER_RED_RATIO_DOWN
                    or ba.redness >= BARRIER_REDNESS_DOWN)
    barrier_state = State.DOWN if barrier_down else State.UP

    crossing_state = State.CLOSED if wig_wag_state == State.ON else State.OPEN

    if crossing_state == State.CLOSED and barrier_state == State.DOWN:
        confidence = 0.95
    elif crossing_state == State.OPEN and barrier_state == State.UP:
        confidence = 0.95
    else:
        confidence = 0.85

    return FrameDetection(
        wig_wag_left=wl, wig_wag_right=wr, barrier_arm=ba,
        wig_wag_state=wig_wag_state, barrier_state=barrier_state,
        crossing_state=crossing_state, confidence=confidence,
    )
