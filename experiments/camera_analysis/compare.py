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

    # Match: greedy 1:1 — each predictor matched at most once, paired
    # to the closest still-unmatched camera closure within ±600s. We
    # build all candidate (camera, predictor) pairs, sort by absolute
    # time difference, and take pairs in order skipping any whose
    # camera or predictor is already used. This avoids the inflated
    # "matched" count we get when several split camera closures all
    # claim the same predictor closure as their neighbour.
    candidates = []
    for ci, c in enumerate(camera_closures):
        cam_start = datetime.fromisoformat(c["started_at"]).astimezone(timezone.utc)
        for pi, (ps, pe) in enumerate(pred_closures):
            diff = (ps - cam_start).total_seconds()
            if abs(diff) > 600:
                continue
            candidates.append((abs(diff), diff, ci, pi))
    candidates.sort(key=lambda x: x[0])

    cam_to_pred: dict[int, tuple[int, float]] = {}
    used_cam: set[int] = set()
    used_pred: set[int] = set()
    for _, diff, ci, pi in candidates:
        if ci in used_cam or pi in used_pred:
            continue
        cam_to_pred[ci] = (pi, diff)
        used_cam.add(ci)
        used_pred.add(pi)

    matches = []
    for ci, c in enumerate(camera_closures):
        if ci in cam_to_pred:
            pi, diff = cam_to_pred[ci]
            matches.append({
                "camera_start": c["started_at"],
                "camera_end": c["ended_at"],
                "predictor_first_close": pred_closures[pi][0].isoformat(),
                "lead_secs": diff,
            })
        else:
            matches.append({
                "camera_start": c["started_at"],
                "camera_end": c["ended_at"],
                "predictor_first_close": None,
                "lead_secs": None,
            })

    matched = len(used_cam)
    return {
        "status": "ok",
        "predictor_closures": len(pred_closures),
        "matched": matched,
        "camera_only": len(camera_closures) - matched,
        "predictor_only": len(pred_closures) - len(used_pred),
        "matches": matches,
    }
