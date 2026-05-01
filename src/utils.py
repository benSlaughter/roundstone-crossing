"""
Shared utilities for crossing prediction and window merging.
"""

from datetime import datetime, timedelta


def merge_closure_windows(
    predictions: list[dict],
    pre_closure_secs: float,
    crossing_clearance_secs: float,
    post_clearance_secs: float,
) -> list[dict]:
    """Build per-train closure windows and merge overlapping ones.

    Each prediction dict must have a ``crossing_eta`` key (a datetime).
    Any other keys are preserved and collected into a ``trains`` list on
    the merged window.

    Returns a list of merged window dicts, each with:
        close_at (datetime), open_at (datetime), trains (list[dict])
    """
    if not predictions:
        return []

    sorted_preds = sorted(predictions, key=lambda p: p["crossing_eta"])

    windows: list[dict] = []
    for pred in sorted_preds:
        eta = pred["crossing_eta"]
        close_at = eta - timedelta(seconds=pre_closure_secs)
        open_at = eta + timedelta(seconds=crossing_clearance_secs + post_clearance_secs)
        windows.append({
            "close_at": close_at,
            "open_at": open_at,
            "trains": [pred],
        })

    merged: list[dict] = [windows[0]]
    for w in windows[1:]:
        if w["close_at"] <= merged[-1]["open_at"]:
            merged[-1]["open_at"] = max(merged[-1]["open_at"], w["open_at"])
            merged[-1]["trains"].extend(w["trains"])
        else:
            merged.append(w)

    return merged
