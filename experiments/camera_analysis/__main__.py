"""CLI entry point: python -m experiments.camera_analysis <command>"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from datetime import datetime, timezone

from .frames import FrameIterator
from . import commands


def load_site(site_dir: Path) -> dict:
    cfg_path = site_dir / "site.yaml"
    if not cfg_path.exists():
        sys.exit(f"site.yaml not found in {site_dir}")
    with open(cfg_path) as f:
        return yaml.safe_load(f)


def build_iterator(site_dir: Path, site_cfg: dict) -> FrameIterator:
    interval = site_cfg.get("site", {}).get("interval_secs", 5.0)
    sources = [site_dir / s for s in site_cfg.get("source_files", [])]
    if not sources:
        sys.exit("site.yaml `source_files` is empty")
    starts = site_cfg.get("valid_range", {}).get("starts_at")
    if not starts:
        sys.exit("site.yaml `valid_range.starts_at` is required")
    anchor = datetime.fromisoformat(starts)
    return FrameIterator(sources, anchor_time=anchor, interval_secs=interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m experiments.camera_analysis")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sample = sub.add_parser("sample", help="Extract a single frame at a given index or time")
    p_sample.add_argument("site_dir", type=Path)
    p_sample.add_argument("--index", type=int, help="Global frame index (0-based)")
    p_sample.add_argument("--at", help="HH:MM[:SS] local time of frame to extract")
    p_sample.add_argument("--out", type=Path, help="Output JPEG path")

    p_ocr = sub.add_parser("ocr-check", help="OCR timestamps at sample frames to detect drift")
    p_ocr.add_argument("site_dir", type=Path)
    p_ocr.add_argument("--every", type=int, default=720,
                       help="OCR every Nth frame (default 720 = 1hr at 5s)")

    p_preview = sub.add_parser("preview-rois",
                               help="Render the configured ROIs over a sample frame")
    p_preview.add_argument("site_dir", type=Path)
    p_preview.add_argument("--index", type=int, default=0)
    p_preview.add_argument("--out", type=Path)

    p_run = sub.add_parser("run", help="Full analysis: detections + quality + comparison")
    p_run.add_argument("site_dir", type=Path)
    p_run.add_argument("--limit", type=int, help="Limit to first N frames (debugging)")

    args = parser.parse_args(argv)

    site_cfg = load_site(args.site_dir)
    it = build_iterator(args.site_dir, site_cfg)

    if args.cmd == "sample":
        return commands.sample(args, it, site_cfg)
    if args.cmd == "ocr-check":
        return commands.ocr_check(args, it, site_cfg)
    if args.cmd == "preview-rois":
        return commands.preview_rois(args, it, site_cfg)
    if args.cmd == "run":
        return commands.run_full(args, it, site_cfg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
