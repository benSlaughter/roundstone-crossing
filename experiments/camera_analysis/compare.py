"""Compare camera-detected closures against the predictor's history DB."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_DB = Path(__file__).resolve().parents[2] / "crossing.db"


def compare_to_predictions(camera_closures: list[dict], site_cfg: dict,
                           db_path: Path = DEFAULT_DB) -> dict:
    """Match camera-detected closure events against the predictor's
    `predictions` table.

    For each camera closure (the truth), find the predictor's first
    'closing_predicted' or 'closed_inferred' state within ±300s and
    report the lead/lag time.
    """
    if not db_path.exists():
        return {"status": f"DB not found at {db_path}"}

    if not camera_closures:
        return {"status": "no camera closures to compare"}

    valid = site_cfg.get("valid_range") or {}
    if not valid.get("starts_at") or not valid.get("ends_at"):
        return {"status": "site.yaml valid_range missing"}

    starts = datetime.fromisoformat(valid["starts_at"]).astimezone(timezone.utc)
    ends = datetime.fromisoformat(valid["ends_at"]).astimezone(timezone.utc)

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    try:
        # Pull predictor closure starts within the window
        rows = db.execute("""
            SELECT timestamp, state, confidence, predicted_change_at
            FROM predictions
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp
        """, (starts.isoformat(), ends.isoformat())).fetchall()
    finally:
        db.close()

    if not rows:
        return {
            "status": "ok",
            "predictor_closures": 0,
            "matched": 0,
            "camera_only": len(camera_closures),
            "predictor_only": 0,
            "matches": [],
            "note": "no predictor data in window — was the server running?",
        }

    # Find predictor closure intervals: contiguous runs where state
    # is one of the closure-related states. Filter out very short runs
    # (< MIN_RUN_SECS) — those are typically brief CLOSING_PREDICTED
    # flickers that revert immediately, not real closure events.
    MIN_RUN_SECS = 30
    closure_states = {"closing_predicted", "closed_inferred"}
    pred_closures: list[tuple[datetime, datetime]] = []
    in_run = False
    run_start = None
    last_ts = None
    for r in rows:
        ts = datetime.fromisoformat(r["timestamp"]).astimezone(timezone.utc)
        if r["state"] in closure_states and not in_run:
            in_run = True
            run_start = ts
        elif r["state"] not in closure_states and in_run:
            in_run = False
            if (last_ts - run_start).total_seconds() >= MIN_RUN_SECS:
                pred_closures.append((run_start, last_ts))
            run_start = None
        last_ts = ts
    if in_run and run_start and last_ts:
        if (last_ts - run_start).total_seconds() >= MIN_RUN_SECS:
            pred_closures.append((run_start, last_ts))

    # Match by interval OVERLAP (with a small tolerance), not by start-time
    # proximity. The earlier greedy-by-start-time approach paired each
    # camera closure to the nearest predictor START — but a long predictor
    # CLOSED_INFERRED span that bridges several back-to-back trains is
    # ONE event from the predictor's perspective and MULTIPLE events from
    # the camera's. Overlap matching correctly counts that as "all camera
    # events matched, predictor was right the whole time" instead of
    # "predictor missed N-1 closures".
    #
    # Two intervals match if their (interval ± OVERLAP_TOLERANCE_SECS)
    # boxes intersect. A single predictor interval may match multiple
    # camera intervals and vice-versa.
    OVERLAP_TOLERANCE_SECS = 60

    def _overlaps(a_start, a_end, b_start, b_end) -> bool:
        if a_end is None:
            a_end = a_start + timedelta(seconds=300)
        if b_end is None:
            b_end = b_start + timedelta(seconds=300)
        pad = timedelta(seconds=OVERLAP_TOLERANCE_SECS)
        return not (a_end + pad < b_start or b_end + pad < a_start)

    matches = []
    matched_pred_indices: set[int] = set()
    for c in camera_closures:
        cam_start = datetime.fromisoformat(c["started_at"]).astimezone(timezone.utc)
        cam_end = (datetime.fromisoformat(c["ended_at"]).astimezone(timezone.utc)
                   if c.get("ended_at") else None)

        # First overlapping predictor interval wins for the timing-diff
        # column; all overlapping intervals are marked "matched".
        first_match_idx: int | None = None
        for pi, (ps, pe) in enumerate(pred_closures):
            if _overlaps(cam_start, cam_end, ps, pe):
                matched_pred_indices.add(pi)
                if first_match_idx is None:
                    first_match_idx = pi

        if first_match_idx is not None:
            ps = pred_closures[first_match_idx][0]
            matches.append({
                "camera_start": c["started_at"],
                "camera_end": c["ended_at"],
                "predictor_first_close": ps.isoformat(),
                "lead_secs": (ps - cam_start).total_seconds(),
            })
        else:
            matches.append({
                "camera_start": c["started_at"],
                "camera_end": c["ended_at"],
                "predictor_first_close": None,
                "lead_secs": None,
            })

    matched_cam_count = sum(1 for m in matches if m["predictor_first_close"])
    return {
        "status": "ok",
        "predictor_closures": len(pred_closures),
        "matched": matched_cam_count,
        "camera_only": len(camera_closures) - matched_cam_count,
        "predictor_only": len(pred_closures) - len(matched_pred_indices),
        "matches": matches,
    }
