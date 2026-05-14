"""OCR the burned-in timestamp from a frame for drift validation.

The CY50 prints the timestamp into the bottom-right corner of every frame
in the format `YYYY/MM/DD HH:MM:SS`. We OCR a handful of frames per
recording (first frame + one per hour) to:

  1. Anchor the wall-clock time of the first frame
  2. Detect clock drift across the recording
  3. Catch any timestamp gaps that suggest skipped frames
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import cv2
import numpy as np
import pytesseract


# Approximate bounding box for the CY50 timestamp string, in pixels for
# 1920x1080 frames. Generous left padding so the year doesn't get clipped.
DEFAULT_TIMESTAMP_BOX = (1100, 1040, 800, 40)   # x, y, w, h

TIMESTAMP_RE = re.compile(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})\s*(\d{1,2})[:\-](\d{2})[:\-](\d{2})")


@dataclass
class TimestampRead:
    raw_text: str
    parsed: Optional[datetime]
    confidence: float       # rough confidence 0..1


def read_timestamp(frame_bgr: np.ndarray,
                   roi: tuple[int, int, int, int] = DEFAULT_TIMESTAMP_BOX,
                   expected: Optional[datetime] = None,
                   ) -> TimestampRead:
    """OCR the timestamp from a single frame.

    Returns the raw OCR text and the parsed datetime (None if unparseable).
    Uses a tight crop + thresholding to maximise OCR accuracy.

    If `expected` is provided, the year/month/day from `expected` are
    substituted whenever the OCR'd date components fail a sanity check
    (year not in 2000–2100, month not 1–12, day not 1–31). This is for
    drift validation where the time-of-day is the meaningful signal and
    "2026" is occasionally misread as "9996" / "9026" by Tesseract.
    """
    x, y, w, h = roi
    # Clamp to frame
    H, W = frame_bgr.shape[:2]
    x2 = min(x + w, W)
    y2 = min(y + h, H)
    crop = frame_bgr[y:y2, x:x2]
    if crop.size == 0:
        return TimestampRead("", None, 0.0)

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # The timestamp is bright text on a near-black bar — invert + binarise
    # to give Tesseract clean black-on-white characters. Threshold 220
    # gives crisp character edges (lower thresholds bleed glow into chars,
    # making "2"s read as "9"s on this camera).
    _, binary = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY)
    # Upscale to give Tesseract more pixels per character
    h_b, w_b = binary.shape
    binary = cv2.resize(binary, (w_b * 3, h_b * 3), interpolation=cv2.INTER_CUBIC)

    raw = pytesseract.image_to_string(
        binary,
        config="--psm 7 -c tessedit_char_whitelist=0123456789/:- ",
    ).strip()

    m = TIMESTAMP_RE.search(raw)
    if not m:
        return TimestampRead(raw, None, 0.0)

    try:
        year, month, day, hour, minute, second = (int(g) for g in m.groups())

        confidence = 1.0
        if not (2000 <= year <= 2100) or not (1 <= month <= 12) or not (1 <= day <= 31):
            if expected is None:
                return TimestampRead(raw, None, 0.0)
            year, month, day = expected.year, expected.month, expected.day
            confidence = 0.7   # date came from fallback, time from OCR

        parsed = datetime(year, month, day, hour, minute, second)
        return TimestampRead(raw, parsed, confidence)
    except (ValueError, OverflowError):
        return TimestampRead(raw, None, 0.0)
