"""Extract validation frames for manual review.

Picks frames from several categories so the user can verify what the
detector saw. For each picked frame we save:

  - <name>.jpg                — full frame (with ROIs drawn)
  - <name>_zoom.jpg           — wig-wag housings cropped large

A `manifest.csv` lists every saved frame, what the detector said, and
provides empty columns for the user to fill in their ground-truth call.

Usage:
  python -m scripts.extract_validation_frames data/camera_sites/site_01_2026-05-14
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from experiments.camera_analysis.frames import FrameIterator   # noqa: E402


def _draw_rois(frame, rois):
    out = frame.copy()
    colors = {
        "wig_wag_left": (0, 255, 255),
        "wig_wag_right": (0, 255, 255),
        "barrier_arm": (0, 255, 0),
    }
    for name, color in colors.items():
        x, y, w, h = rois[name]
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
        cv2.putText(out, name, (x, y - 6), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, color, 1)
    return out


def _zoom_panel(frame, rois, scale=4):
    """Crop wig-wag housings 4x larger so a human can see the LEDs."""
    crops = []
    for name in ("wig_wag_left", "wig_wag_right"):
        x, y, w, h = rois[name]
        # Pad context around the ROI
        pad = 30
        H, W = frame.shape[:2]
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(W, x + w + pad)
        y1 = min(H, y + h + pad)
        crop = frame[y0:y1, x0:x1].copy()
        # Draw the ROI rectangle inside the crop
        cv2.rectangle(crop,
                      (x - x0, y - y0),
                      (x - x0 + w, y - y0 + h),
                      (0, 255, 255), 2)
        ch, cw = crop.shape[:2]
        crop = cv2.resize(crop, (cw * scale, ch * scale),
                          interpolation=cv2.INTER_NEAREST)
        cv2.putText(crop, name, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (255, 255, 255), 2)
        crops.append(crop)

    # Pad to same height, hstack
    h = max(c.shape[0] for c in crops)
    padded = []
    for c in crops:
        if c.shape[0] < h:
            pad = np.zeros((h - c.shape[0], c.shape[1], 3), dtype=c.dtype)
            c = np.vstack([c, pad])
        padded.append(c)
    return np.hstack(padded)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("site_dir", type=Path)
    args = ap.parse_args()

    site_dir = args.site_dir
    site_cfg = yaml.safe_load((site_dir / "site.yaml").read_text())
    rois = site_cfg["roi"]
    interval = site_cfg["site"]["interval_secs"]

    sources = [site_dir / f for f in site_cfg["source_files"]]
    from datetime import datetime as _dt
    anchor = _dt.fromisoformat(site_cfg["valid_range"]["starts_at"])
    it = FrameIterator(sources, anchor_time=anchor, interval_secs=interval)

    # Load the detector's per-frame call for context
    by_idx = {}
    with open(site_dir / "detections.csv") as f:
        for r in csv.DictReader(f):
            by_idx[int(r["global_index"])] = r

    out_dir = site_dir / "validation"
    out_dir.mkdir(exist_ok=True)

    # Categories of frames worth validating. Each entry: (name, frame_index, why)
    # Frame index = ((target_time - 08:54:55) / 5s)
    # We anchor against the start time directly.
    picks: list[tuple[str, int, str]] = [
        # Boundaries of the largest merged closures — verify the merge is correct
        ("c01_start_08-58-55", 48, "closure 1 start (was first closure of day)"),
        ("c01_end_09-10-55",   192, "closure 1 end (large merged event 720s)"),
        ("c01_mid_09-04-55",   120, "closure 1 mid (verify still closed mid-event)"),

        # Closure 3 — biggest merged event 2250s = 37.5min
        ("c03_start_09-15-45", 250, "closure 3 start (long merged 2250s)"),
        ("c03_mid_09-34-30",   476, "closure 3 mid 1 (37min closure plausible?)"),
        ("c03_mid_09-44-30",   596, "closure 3 mid 2"),
        ("c03_end_09-53-15",   701, "closure 3 end"),

        # Open between closures — verify these gaps are real
        ("open_09-12-30",      213, "between c1 and c2 — verify open"),
        ("open_09-54-30",      716, "between c3 and c4 — verify open"),
        ("open_10-58-30",      1500, "between c7 and c8 — verify open"),

        # Short closure (40s) — could be false positive
        ("c02_short_09-14-20", 233, "short 40s closure — real or noise?"),

        # Camera-only closure (predictor missed) — closure 8 starts 11:01:35 UTC
        # Wait that's c8 in matches list. In events list c8 = 11:01:35
        ("c08_camera_only_11-01-35", 1500, "camera-only event: did predictor miss this?"),

        # Late-recording stuck-on-closed problem — wig=on but redness low
        ("late_13-09-55", 3060, "13:09 'closed' — wig_left red ratio 0.084 but redness 0.0097"),
        ("late_13-29-55", 3300, "13:29 'closed' — wl=0.026 wr=0.040, just above threshold"),
        ("late_14-00-00", 3661, "14:00 'closed' — verify or false positive?"),
        ("late_14-30-00", 4021, "14:30 'closed' — verify or false positive?"),
        ("late_15-00-00", 4381, "15:00 'closed' — verify or false positive?"),
        ("late_15-20-00", 4621, "15:20 'closed' — verify or false positive?"),

        # Open frame inside a long closure (the merge boundaries) — wig=off+barr=down
        ("disagree_09-36-55_wigOFF_barrDOWN", 504, "wig OFF barrier DOWN inside closure 3"),
        ("disagree_09-44-30",    596, "mid closure 3"),

        # Strong unambiguous reference frames
        ("ref_open_at_start",     0, "first frame, definitely open"),
        ("ref_strong_closure_08-58-55",  48, "first closure of day - strong reference"),
    ]

    # Dedupe by frame index
    seen = set()
    picks_unique = []
    for name, idx, why in picks:
        if idx in seen:
            continue
        seen.add(idx)
        picks_unique.append((name, idx, why))

    manifest_rows = []
    for name, idx, why in picks_unique:
        ref = it.sample(idx)
        if ref is None:
            print(f"  SKIP {name}: frame {idx} not available")
            continue

        full_jpg = out_dir / f"{name}.jpg"
        zoom_jpg = out_dir / f"{name}_zoom.jpg"

        full = _draw_rois(ref.frame, rois)
        cv2.imwrite(str(full_jpg), full)
        cv2.imwrite(str(zoom_jpg), _zoom_panel(ref.frame, rois))

        det = by_idx.get(idx, {})
        manifest_rows.append({
            "filename": name,
            "frame_index": idx,
            "real_time": ref.real_time.isoformat(),
            "category": why,
            "detector_state": det.get("crossing_state", "?"),
            "wig_wag_state": det.get("wig_wag_state", "?"),
            "barrier_state": det.get("barrier_state", "?"),
            "confidence": det.get("confidence", "?"),
            "wl_red_ratio": det.get("wl_red_ratio", "?"),
            "wl_redness": det.get("wl_redness", "?"),
            "wr_red_ratio": det.get("wr_red_ratio", "?"),
            "wr_redness": det.get("wr_redness", "?"),
            "ba_red_ratio": det.get("ba_red_ratio", "?"),
            "ba_redness": det.get("ba_redness", "?"),
            "ground_truth_state": "",   # user fills in: open / closed / closing
            "ground_truth_wig_wag": "", # user fills in: on / off
            "ground_truth_barrier": "", # user fills in: up / down / partial
            "notes": "",
        })
        print(f"  {name}  ({ref.real_time.time()})")

    # Manifest CSV
    manifest = out_dir / "manifest.csv"
    with open(manifest, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest_rows[0].keys()))
        w.writeheader()
        w.writerows(manifest_rows)

    print(f"\n{len(manifest_rows)} frames extracted to {out_dir}")
    print(f"Manifest: {manifest}")
    print()
    print("How to use:")
    print("  1. Look at each <name>.jpg (full frame with ROIs) and <name>_zoom.jpg (wig-wag close-up)")
    print("  2. Fill in ground_truth_state/wig_wag/barrier columns in manifest.csv")
    print("  3. Tell me when done so I can compare and tune thresholds")


if __name__ == "__main__":
    main()
