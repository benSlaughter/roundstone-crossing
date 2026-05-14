"""CLI command implementations."""

from __future__ import annotations

from datetime import datetime, time as dtime
from pathlib import Path

import cv2

from .frames import FrameIterator
from .ocr import read_timestamp


def sample(args, it: FrameIterator, site_cfg: dict) -> int:
    """Extract a single frame to disk for visual inspection."""
    if args.index is not None:
        ref = it.sample(args.index)
        if ref is None:
            print(f"Frame index {args.index} out of range")
            return 1
    elif args.at:
        # Parse HH:MM or HH:MM:SS
        parts = args.at.split(":")
        if len(parts) == 2:
            t = dtime(int(parts[0]), int(parts[1]))
        elif len(parts) == 3:
            t = dtime(int(parts[0]), int(parts[1]), int(parts[2]))
        else:
            print("--at must be HH:MM or HH:MM:SS")
            return 1
        anchor = it.anchor_time
        target = datetime.combine(anchor.date(), t, tzinfo=anchor.tzinfo)
        delta = (target - anchor).total_seconds()
        if delta < 0:
            print(f"Requested time {args.at} is before anchor {anchor.time()}")
            return 1
        index = int(delta / it.interval_secs)
        ref = it.sample(index)
        if ref is None:
            print(f"Frame index {index} (for time {args.at}) out of range")
            return 1
    else:
        print("Provide --index or --at")
        return 1

    out = args.out or (args.site_dir / "frames" / f"sample_{ref.global_index:05d}.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), ref.frame)
    print(f"Frame {ref.global_index} ({ref.real_time.isoformat()}) → {out}")
    return 0


def ocr_check(args, it: FrameIterator, site_cfg: dict) -> int:
    """OCR the timestamp of sampled frames to estimate clock drift."""
    total = it.total_frames()
    sample_indices = list(range(0, total, args.every))
    if (total - 1) not in sample_indices:
        sample_indices.append(total - 1)

    print(f"Sampling {len(sample_indices)} frames out of {total} total")
    print(f"{'idx':>6} {'expected':<25} {'ocr_raw':<24} {'parsed':<25} {'drift_s':>8}")
    print("-" * 95)

    drifts = []
    for idx in sample_indices:
        ref = it.sample(idx)
        if ref is None:
            continue
        result = read_timestamp(ref.frame)
        expected = ref.real_time
        if result.parsed:
            # OCR result is naive (no tz), assume same tz as expected
            ocr_dt = result.parsed.replace(tzinfo=expected.tzinfo)
            drift = (ocr_dt - expected).total_seconds()
            drifts.append(drift)
            drift_str = f"{drift:+.0f}"
        else:
            drift_str = "—"
        print(f"{idx:>6} {expected.isoformat():<25} {result.raw_text!r:<24} "
              f"{str(result.parsed):<25} {drift_str:>8}")

    if drifts:
        avg = sum(drifts) / len(drifts)
        rng = max(drifts) - min(drifts)
        print(f"\nAvg drift: {avg:+.1f}s   Range: {rng:.1f}s   Samples: {len(drifts)}")
    return 0


def preview_rois(args, it: FrameIterator, site_cfg: dict) -> int:
    """Render configured ROIs as boxes over a sample frame."""
    ref = it.sample(args.index)
    if ref is None:
        print(f"Frame {args.index} out of range")
        return 1
    img = ref.frame.copy()
    rois = site_cfg.get("roi", {}) or {}
    colors = {
        "barrier_arm": (0, 255, 255),
        "barrier_up_arm": (0, 200, 200),
        "barrier_down_arm": (0, 255, 255),
        "wig_wag_left": (0, 0, 255),
        "wig_wag_right": (0, 0, 255),
        "exclude_timestamp_bar": (128, 128, 128),
    }
    for name, box in rois.items():
        if not box:
            continue
        x, y, w, h = box
        color = colors.get(name, (255, 255, 0))
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
        cv2.putText(img, name, (x, max(0, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

    out = args.out or (args.site_dir / "frames" / f"preview_rois_{ref.global_index:05d}.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), img)
    print(f"ROI preview → {out}")
    return 0


def run_full(args, it: FrameIterator, site_cfg: dict) -> int:
    """Full analysis pipeline."""
    from .pipeline import run as run_pipeline
    return run_pipeline(args, it, site_cfg)
