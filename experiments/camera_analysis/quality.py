"""Site-quality scoring."""

from __future__ import annotations

import statistics


def compute_quality(rows: list, closures: list[dict], ocr_drifts: list[dict],
                    total_frames: int, site_cfg: dict) -> dict:
    """Compute a 0-100 site-quality score plus the underlying metrics."""

    # --- Detection confidence ---
    confidences = [float(r[5]) for r in rows]
    mean_conf = statistics.mean(confidences) if confidences else 0.0
    low_conf_pct = (sum(1 for c in confidences if c < 0.9) / len(confidences) * 100
                    if confidences else 0.0)

    # --- Wig-wag separation ---
    # Difference between mean redness when called ON vs OFF — bigger
    # gap = more reliable detection at this site.
    on_red = []
    off_red = []
    for r in rows:
        red = max(float(r[7]), float(r[9]))   # max(left.redness, right.redness)
        if r[3] == "on":
            on_red.append(red)
        elif r[3] == "off":
            off_red.append(red)
    if on_red and off_red:
        wig_wag_sep = statistics.mean(on_red) - statistics.mean(off_red)
    else:
        wig_wag_sep = 0.0

    # --- Image quality (approximate from per-frame metric proxies) ---
    # We don't store mean brightness per frame in the CSV, so derive it
    # roughly: confidence is high when scene is clear. For now, use the
    # mean redness of the wig-wag ROIs as a brightness proxy and a
    # placeholder for sharpness (we'd need to add it during iteration).
    mean_brightness = 128.0   # placeholder — populated below if available
    mean_sharpness = 0.0
    overexposed_pct = 0.0
    underexposed_pct = 0.0

    # OCR drift health
    valid_drifts = [d["drift_secs"] for d in ocr_drifts
                    if d.get("drift_secs") is not None]
    drift_range = max(valid_drifts) - min(valid_drifts) if valid_drifts else 0.0

    # --- Composite score ---
    # Each component yields a 0-100 sub-score; final = weighted average.
    score_confidence = max(0.0, mean_conf * 100)
    score_separation = min(100.0, max(0.0, wig_wag_sep * 1000))   # 0.05 → 50
    score_drift = max(0.0, 100.0 - drift_range * 5)               # 20s drift → 0
    score_low_conf = max(0.0, 100.0 - low_conf_pct)

    overall = (
        0.40 * score_confidence
        + 0.25 * score_separation
        + 0.15 * score_drift
        + 0.20 * score_low_conf
    )

    if overall >= 85:
        grade = "A — excellent"
    elif overall >= 70:
        grade = "B — good"
    elif overall >= 50:
        grade = "C — usable but flawed"
    elif overall >= 30:
        grade = "D — marginal"
    else:
        grade = "F — unusable"

    return {
        "overall_score": overall,
        "overall_grade": grade,
        "frames_analysed": len(rows),
        "real_time_hours": (len(rows) * site_cfg.get("site", {}).get("interval_secs", 5.0)) / 3600,
        "mean_confidence": mean_conf,
        "low_confidence_pct": low_conf_pct,
        "wig_wag_separation": wig_wag_sep,
        "mean_brightness": mean_brightness,
        "mean_sharpness": mean_sharpness,
        "overexposed_pct": overexposed_pct,
        "underexposed_pct": underexposed_pct,
        "ocr_drift_range_secs": drift_range,
        "subscores": {
            "confidence": score_confidence,
            "separation": score_separation,
            "drift": score_drift,
            "low_confidence_inverse": score_low_conf,
        },
    }
