"""Extract validation frames for the 13 camera-only closure events.

These are closures the camera detected but the predictor did not match.
They might be:
  - Real closures the predictor missed
  - Camera false positives we haven't validated yet

For each, we extract a frame from the middle of the event so the user
can confirm visually whether the crossing was actually closed.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from experiments.camera_analysis.frames import FrameIterator   # noqa: E402


# Camera-only closures from the v2 report (start_BST, duration_secs, label)
CO_EVENTS = [
    ("11:02:35", 150,  "co_event_01__150s"),
    ("11:07:25", 335,  "co_event_02__335s"),
    ("11:23:00", 220,  "co_event_03__220s"),
    ("11:44:40", 485,  "co_event_04__485s"),
    ("12:09:20", 280,  "co_event_05__280s"),
    ("12:16:30", 360,  "co_event_06__360s"),
    ("13:05:15", 100,  "co_event_07__100s"),
    ("14:08:05", 270,  "co_event_08__270s"),
    ("14:28:25", 175,  "co_event_09__175s"),
    ("14:34:40", 170,  "co_event_10__170s"),
    ("14:48:05", 1015, "co_event_11__1015s_LONG"),    # 17 min — also sample boundaries
    ("15:08:30", 100,  "co_event_12__100s"),
    ("15:12:20", 80,   "co_event_13__80s"),
]


def _draw_rois(frame, rois):
    out = frame.copy()
    for name, color in [("wig_wag_left", (0,255,255)),
                        ("wig_wag_right", (0,255,255)),
                        ("barrier_arm", (0,255,0))]:
        x, y, w, h = rois[name]
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
        cv2.putText(out, name, (x, y - 6), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, color, 1)
    return out


def _zoom_panel(frame, rois, scale=4):
    crops = []
    for name in ("wig_wag_left", "wig_wag_right"):
        x, y, w, h = rois[name]
        pad = 30
        H, W = frame.shape[:2]
        x0 = max(0, x - pad); y0 = max(0, y - pad)
        x1 = min(W, x + w + pad); y1 = min(H, y + h + pad)
        crop = frame[y0:y1, x0:x1].copy()
        cv2.rectangle(crop, (x - x0, y - y0), (x - x0 + w, y - y0 + h),
                      (0, 255, 255), 2)
        ch, cw = crop.shape[:2]
        crop = cv2.resize(crop, (cw * scale, ch * scale),
                          interpolation=cv2.INTER_NEAREST)
        cv2.putText(crop, name, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (255,255,255), 2)
        crops.append(crop)
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
    anchor = datetime.fromisoformat(site_cfg["valid_range"]["starts_at"])
    it = FrameIterator(sources, anchor_time=anchor, interval_secs=interval)

    by_idx = {}
    with open(site_dir / "detections.csv") as f:
        for r in csv.DictReader(f):
            by_idx[int(r["global_index"])] = r

    out_dir = site_dir / "validation"
    out_dir.mkdir(exist_ok=True)

    def time_to_index(time_str: str) -> int:
        # time_str = "HH:MM:SS" in BST (anchor tz)
        h, m, s = (int(p) for p in time_str.split(":"))
        target = anchor.replace(hour=h, minute=m, second=s)
        delta = (target - anchor).total_seconds()
        return int(delta / interval)

    picks: list[tuple[str, int, str]] = []
    for start_str, duration, label in CO_EVENTS:
        start_idx = time_to_index(start_str)
        mid_idx = start_idx + int((duration / interval) / 2)
        end_idx = start_idx + int(duration / interval)
        # Mid frame for every event
        picks.append((f"{label}_MID", mid_idx,
                      f"middle of camera-only closure starting {start_str} ({duration}s)"))
        # For the long one, also start & end
        if duration > 600:
            picks.append((f"{label}_START", start_idx,
                          f"START of long camera-only closure {start_str}"))
            picks.append((f"{label}_END", end_idx - 1,
                          f"END of long camera-only closure (start+{duration}s)"))

    manifest_rows = []
    for name, idx, why in picks:
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
            "wl_led": det.get("wl_led_count", "?"),
            "wr_led": det.get("wr_led_count", "?"),
            "ground_truth_state": "",
            "ground_truth_wig_wag": "",
            "ground_truth_barrier": "",
            "notes": "",
        })
        print(f"  {name}  ({ref.real_time.time()})")

    manifest = out_dir / "manifest.csv"
    with open(manifest, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest_rows[0].keys()))
        w.writeheader()
        w.writerows(manifest_rows)

    print(f"\n{len(manifest_rows)} frames extracted to {out_dir}")
    print(f"Manifest: {manifest}")


if __name__ == "__main__":
    main()
