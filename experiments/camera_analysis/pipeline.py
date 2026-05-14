"""Whole-recording analysis pipeline.

Iterates every frame in a recording session, runs detection, and emits:
  - detections.csv       : per-frame state
  - quality.json         : machine-readable quality metrics
  - quality_report.md    : human-readable summary

Compares detected events against the predictor's `predictions` table
when available.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .frames import FrameIterator
from .detect import detect_frame, State
from .ocr import read_timestamp
from .quality import compute_quality
from .compare import compare_to_predictions


def run(args, it: FrameIterator, site_cfg: dict) -> int:
    site_dir = args.site_dir
    rois = site_cfg.get("roi") or {}
    required = ("wig_wag_left", "wig_wag_right", "barrier_arm")
    missing = [k for k in required if not rois.get(k)]
    if missing:
        print(f"site.yaml is missing ROI(s): {missing}")
        return 1

    valid = site_cfg.get("valid_range") or {}
    valid_until = (datetime.fromisoformat(valid["ends_at"])
                   if valid.get("ends_at") else None)

    # --- Frame iteration + detection ---
    detections_path = site_dir / "detections.csv"
    print(f"Processing → {detections_path}")
    total = it.total_frames()
    print(f"Total frames: {total}")

    rows = []
    last_state = None
    transition_count = 0

    with open(detections_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "global_index", "real_time", "crossing_state",
            "wig_wag_state", "barrier_state", "confidence",
            "wl_red_ratio", "wl_redness", "wl_led_count",
            "wr_red_ratio", "wr_redness", "wr_led_count",
            "ba_red_ratio", "ba_redness",
        ])
        for ref in it:
            if args.limit and ref.global_index >= args.limit:
                break
            if valid_until and ref.real_time > valid_until:
                print(f"Stopping at frame {ref.global_index} ({ref.real_time}) — past valid_until")
                break

            d = detect_frame(ref.frame, rois)
            row = [
                ref.global_index,
                ref.real_time.isoformat(),
                d.crossing_state.value,
                d.wig_wag_state.value,
                d.barrier_state.value,
                f"{d.confidence:.2f}",
                f"{d.wig_wag_left.red_pixel_ratio:.4f}",
                f"{d.wig_wag_left.redness:.4f}",
                d.wig_wag_left.led_pixel_count,
                f"{d.wig_wag_right.red_pixel_ratio:.4f}",
                f"{d.wig_wag_right.redness:.4f}",
                d.wig_wag_right.led_pixel_count,
                f"{d.barrier_arm.red_pixel_ratio:.4f}",
                f"{d.barrier_arm.redness:.4f}",
            ]
            writer.writerow(row)
            rows.append(row)

            if last_state and d.crossing_state != last_state:
                transition_count += 1
            last_state = d.crossing_state

            if ref.global_index % 200 == 0:
                pct = (ref.global_index + 1) / total * 100
                print(f"  frame {ref.global_index:>5}/{total} ({pct:>5.1f}%)  "
                      f"{ref.real_time.time()}  state={d.crossing_state.value}")

    print(f"\nProcessed {len(rows)} frames")
    print(f"State transitions: {transition_count}")

    # --- Closure events from state transitions ---
    # Pre-filter brief blips (<20s) BEFORE merging to stop them
    # contaminating real closures via the merge step. Then a 60s merge
    # gap collapses in-closure flicker without absorbing genuinely
    # adjacent closures.
    closures = _find_closure_events(rows,
                                    merge_gap_secs=60.0,
                                    min_raw_duration_secs=20.0,
                                    min_duration_secs=30.0)
    print(f"Closure events: {len(closures)}")

    # --- OCR drift check at hourly anchors ---
    ocr_drifts = _ocr_drift_check(it, every_n=720)

    # --- Quality scoring ---
    print("\nComputing quality metrics …")
    quality = compute_quality(rows, closures, ocr_drifts, total_frames=len(rows),
                              site_cfg=site_cfg)
    quality_json = site_dir / "quality.json"
    with open(quality_json, "w") as f:
        json.dump(quality, f, indent=2, default=str)
    print(f"→ {quality_json}")

    # --- Comparison with predictions DB ---
    print("\nComparing against predictions DB …")
    comparison = compare_to_predictions(closures, site_cfg=site_cfg)

    # --- Human-readable report ---
    report_path = site_dir / "quality_report.md"
    _write_report(report_path, site_cfg, quality, comparison, closures, ocr_drifts)
    print(f"→ {report_path}")

    return 0


def _find_closure_events(rows: list,
                         merge_gap_secs: float = 60.0,
                         min_raw_duration_secs: float = 20.0,
                         min_duration_secs: float = 30.0) -> list[dict]:
    """Identify continuous CLOSED runs as discrete closure events.

    Three stages of post-processing remove flicker artefacts and
    distinguish overlapping real closures:

      1. **Pre-filter**: drop raw closures shorter than
         `min_raw_duration_secs`. These are typically 5-15 second
         spurious wig-wag readings caused by sun glints / brief
         occlusion ambiguities. Dropping them BEFORE merging is
         critical — otherwise a brief blip near a real closure gets
         merged in and contaminates the closure boundaries.

      2. **Merge** consecutive surviving closures separated by less
         than `merge_gap_secs` of OPEN. With 5-second sampling the
         wig-wag-OFF runs inside a real closure are typically <15s
         (one alternation cycle missed). Real distinct closures (e.g.
         back-to-back trains) are usually 60s+ apart so 60s is a safe
         merge gap that catches in-closure flicker without absorbing
         genuinely separate closures.

      3. **Drop** merged closures shorter than `min_duration_secs`.
    """
    # Stage 0: build raw events
    raw: list[dict] = []
    in_closure = False
    start = None
    last_ts = None
    for row in rows:
        ts = datetime.fromisoformat(row[1])
        state = row[2]
        if state == "closed" and not in_closure:
            in_closure = True
            start = ts
        elif state != "closed" and in_closure:
            in_closure = False
            raw.append({
                "started_at": start,
                "ended_at": ts,
                "duration_secs": (ts - start).total_seconds(),
            })
            start = None
        last_ts = ts
    if in_closure and start:
        raw.append({
            "started_at": start,
            "ended_at": None,
            "duration_secs": None,
        })

    # Stage 1: pre-filter brief blips
    pre_filtered = [
        c for c in raw
        if c["duration_secs"] is None or c["duration_secs"] >= min_raw_duration_secs
    ]

    # Stage 2: merge nearby closures (flicker tolerance)
    merged: list[dict] = []
    for c in pre_filtered:
        if (merged and merged[-1]["ended_at"] is not None
                and (c["started_at"] - merged[-1]["ended_at"]).total_seconds() < merge_gap_secs):
            merged[-1]["ended_at"] = c["ended_at"]
            if c["ended_at"] is not None:
                merged[-1]["duration_secs"] = (
                    c["ended_at"] - merged[-1]["started_at"]).total_seconds()
            else:
                merged[-1]["duration_secs"] = None
        else:
            merged.append(c)

    # Stage 3: drop sub-threshold events
    keep: list[dict] = []
    for c in merged:
        if c["duration_secs"] is None or c["duration_secs"] >= min_duration_secs:
            keep.append({
                "started_at": c["started_at"].isoformat(),
                "ended_at": c["ended_at"].isoformat() if c["ended_at"] else None,
                "duration_secs": c["duration_secs"],
            })
    return keep


def _ocr_drift_check(it: FrameIterator, every_n: int) -> list[dict]:
    """OCR sample frames to estimate clock drift."""
    print(f"\nOCR drift check (every {every_n} frames = {every_n * it.interval_secs / 60:.0f} min)…")
    total = it.total_frames()
    indices = list(range(0, total, every_n))
    if (total - 1) not in indices:
        indices.append(total - 1)

    samples: list[dict] = []
    for idx in indices:
        ref = it.sample(idx)
        if ref is None:
            continue
        expected = ref.real_time
        result = read_timestamp(ref.frame, expected=expected)
        drift = None
        if result.parsed:
            ocr_dt = result.parsed.replace(tzinfo=expected.tzinfo)
            drift = (ocr_dt - expected).total_seconds()
        samples.append({
            "global_index": idx,
            "expected": expected.isoformat(),
            "ocr_raw": result.raw_text,
            "parsed": result.parsed.isoformat() if result.parsed else None,
            "drift_secs": drift,
        })
        d = f"{drift:+.0f}s" if drift is not None else "—"
        print(f"  frame {idx:>5}: expected {expected.time()}  parsed {result.parsed and result.parsed.time()}  drift {d}")
    return samples


def _write_report(path: Path, site_cfg: dict, quality: dict,
                  comparison: dict, closures: list[dict],
                  ocr_drifts: list[dict]) -> None:
    site = site_cfg.get("site", {})
    pos = site_cfg.get("position", {})
    lines = [
        f"# Site quality report: {site.get('id', 'site')}",
        "",
        f"- **Name:** {site.get('name')}",
        f"- **Date:** {site.get('recorded_at')}",
        f"- **Camera:** {site.get('camera')}",
        f"- **Interval:** {site.get('interval_secs')}s",
        f"- **Position:** {pos.get('description')}, ~{pos.get('approx_distance_m')}m, ~{pos.get('approx_height_m')}m up",
        "",
        f"## Overall score: **{quality['overall_score']:.0f} / 100** ({quality['overall_grade']})",
        "",
        "## Detection summary",
        "",
        f"- Total frames analysed: {quality['frames_analysed']:,}",
        f"- Real-world coverage: {quality['real_time_hours']:.1f} hours",
        f"- Closure events detected: **{len(closures)}**",
        f"- Mean detection confidence: {quality['mean_confidence']:.2f}",
        f"- Frames with low confidence: {quality['low_confidence_pct']:.1f}%",
        "",
    ]

    if closures:
        lines.append("### Closure events")
        lines.append("")
        lines.append("| # | Start | End | Duration |")
        lines.append("|---|-------|-----|----------|")
        for i, c in enumerate(closures, 1):
            start = c["started_at"][11:19]
            end = (c["ended_at"][11:19] if c["ended_at"] else "ongoing")
            dur = f"{c['duration_secs']:.0f}s" if c["duration_secs"] else "—"
            lines.append(f"| {i} | {start} | {end} | {dur} |")
        lines.append("")

    lines += [
        "## Image quality",
        "",
        f"- Mean brightness: {quality['mean_brightness']:.0f}/255",
        f"- Mean sharpness (Laplacian variance): {quality['mean_sharpness']:.0f}",
        f"- Frames near-overexposed: {quality['overexposed_pct']:.1f}%",
        f"- Frames near-underexposed: {quality['underexposed_pct']:.1f}%",
        "",
        "## Subject visibility",
        "",
        f"- Wig-wag separation (mean redness gap ON−OFF): {quality['wig_wag_separation']:.3f}",
        f"  (higher = more reliable detection)",
        "",
    ]

    if ocr_drifts:
        valid = [d for d in ocr_drifts if d["drift_secs"] is not None]
        if valid:
            drifts = [d["drift_secs"] for d in valid]
            avg = sum(drifts) / len(drifts)
            rng = max(drifts) - min(drifts)
            lines += [
                "## Clock drift (OCR vs computed time)",
                "",
                f"- Samples: {len(valid)}",
                f"- Mean drift: {avg:+.1f}s",
                f"- Range: {rng:.0f}s",
                "",
            ]

    if comparison.get("status") == "ok":
        lines += [
            "## Comparison vs predictor",
            "",
            f"- Predictor closure events during this window: {comparison['predictor_closures']}",
            f"  (filtered to runs ≥30s; brief CLOSING_PREDICTED flickers ignored)",
            f"- Camera closure events: {len(closures)}",
            f"- Matched closures: {comparison['matched']}",
            f"- Camera-only (predictor missed): {comparison['camera_only']}",
            f"- Predictor-only (false positive): {comparison['predictor_only']}",
            "",
        ]
        if comparison.get("matches"):
            lines.append("### Closure timing accuracy (times shown in UTC)")
            lines.append("")
            lines.append("| # | Camera start (UTC) | Predictor first CLOSING (UTC) | Diff |")
            lines.append("|---|--------------------|-------------------------------|------|")
            for i, m in enumerate(comparison["matches"], 1):
                cam_dt = datetime.fromisoformat(m["camera_start"]).astimezone(timezone.utc)
                cam = cam_dt.strftime("%H:%M:%S")
                if m.get("predictor_first_close"):
                    pred_dt = datetime.fromisoformat(m["predictor_first_close"]).astimezone(timezone.utc)
                    pred = pred_dt.strftime("%H:%M:%S")
                else:
                    pred = "—"
                diff = (f"{m['lead_secs']:+.0f}s"
                        if m.get("lead_secs") is not None else "—")
                lines.append(f"| {i} | {cam} | {pred} | {diff} |")
            lines.append("")
            lines.append("(Negative diff = predictor warned before camera saw closure)")
    else:
        lines += [
            "## Comparison vs predictor",
            "",
            f"- {comparison.get('status')}",
            "",
        ]

    path.write_text("\n".join(lines) + "\n")
