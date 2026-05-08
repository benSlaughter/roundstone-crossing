"""
Coverage metric — measure inferrer accuracy against derived ground truth.

For every moment in time within the analysis window, compute whether the
predictor said the barriers were CLOSED and whether they actually were
CLOSED, then aggregate into a time-weighted confusion matrix.

Ground truth derivation
=======================
We don't yet have continuous device-logged barrier state (waiting on the
ESP32 build), so we derive ground truth from `train_passages` data. The
key assumption: for every train that we observed at the at-crossing
berth, barriers MUST have been down at that moment.

We expand each at-crossing observation into a closure window using the
calibrated timing constants:
  - barriers down `pre_closure_secs` BEFORE at-crossing time
  - barriers raise `crossing_clearance_secs + post_clearance_secs` AFTER

Overlapping windows from consecutive trains are merged. This gives a
continuous timeline of "definitely closed" intervals.

This is a LOWER BOUND on actual closed time — closures with no train
(signaller error, stuck route, road maintenance, etc.) will appear as
"open" in the ground truth and thus penalise the predictor as a false
positive. That's a known limitation; replace with device-logged ground
truth in Phase 2 once the ESP32 is built.

Metrics computed
================
Time-weighted confusion matrix:

                    Reality
                 CLOSED   OPEN
  Pred CLOSED   [ TP ] [ FP ]   FP = "drove through closed-state report"
       OPEN     [ FN ] [ TN ]   FN = "missed closure, no warning"

  Precision = TP / (TP + FP) — when we say CLOSED, how often right?
  Recall    = TP / (TP + FN) — of actual closures, how many caught?
  Accuracy  = (TP + TN) / total
  F1        = 2 * (precision * recall) / (precision + recall)

Plus:
  - Largest false-positive intervals (with reasons) — most actionable output
  - Coverage breakdown: time excluded as STALE_DATA
  - Optional: per-direction, per-hour breakdowns

Usage
=====
  python experiments/coverage_metric.py path/to/db.sqlite
  python experiments/coverage_metric.py path/to/db.sqlite --bucket-secs 5
  python experiments/coverage_metric.py path/to/db.sqlite --pre-closure 180

Predicted-closed states
=======================
By default we treat both CLOSED_INFERRED and CLOSING_PREDICTED as
"predicted closed", because both signal to the user that barriers may
be down. Use --strict to count only CLOSED_INFERRED.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from dateutil.parser import parse as parse_ts
except ImportError:
    print("error: python-dateutil required (pip install python-dateutil)", file=sys.stderr)
    sys.exit(2)


# Predictor states that the user perceives as "barriers down or going down".
# CLOSING_PREDICTED is included because it tells the user "expect closed soon"
# — if barriers are actually open at that moment, that's still a misleading
# signal. Pass --strict to count only CLOSED_INFERRED.
PREDICTED_CLOSED_STATES = {"closed_inferred", "closing_predicted", "opening_predicted"}
PREDICTED_CLOSED_STATES_STRICT = {"closed_inferred", "opening_predicted"}

# State considered "no information" — excluded from the denominator entirely.
EXCLUDED_STATES = {"stale_data", "unknown"}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class StateInterval:
    """A single row from state_intervals — what the predictor said over a span."""
    state: str
    started_at: datetime
    ended_at: datetime  # current time if still open
    confidence: float | None
    reason: str | None


@dataclass
class GroundTruthWindow:
    """A derived window where barriers were definitely down, expanded from a
    train passage observation."""
    closed_from: datetime
    closed_until: datetime
    sources: list[str] = field(default_factory=list)  # headcodes that contributed


@dataclass
class FPInterval:
    """A continuous span where predictor said CLOSED but ground truth says OPEN."""
    start: datetime
    end: datetime
    state: str
    reason: str | None

    @property
    def duration_secs(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass
class FNInterval:
    """A continuous span where ground truth says CLOSED but predictor said OPEN."""
    start: datetime
    end: datetime
    state: str          # what the predictor was saying instead
    sources: list[str]  # headcodes that drove this ground-truth window

    @property
    def duration_secs(self) -> float:
        return (self.end - self.start).total_seconds()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_state_intervals(db: sqlite3.Connection, now: datetime) -> list[StateInterval]:
    """Load all state intervals, ordered chronologically.

    The production state_intervals table can contain overlapping/unclosed
    rows when the app restarts (each restart starts a fresh logger that
    didn't see the prior open interval; tracked as audit finding H1).
    We reconstruct a clean non-overlapping timeline here:
      - sort by started_at
      - for each interval, clamp its effective end to the next interval's
        start (if any), or `now`
      - drop zero/negative-duration entries
    """
    rows = db.execute("""
        SELECT state, started_at, ended_at, confidence, reason
        FROM state_intervals
        ORDER BY started_at
    """).fetchall()

    raw = []
    for state, started, ended, conf, reason in rows:
        start = parse_ts(started)
        end = parse_ts(ended) if ended else now
        raw.append((start, end, state, conf, reason))

    raw.sort(key=lambda r: r[0])

    out: list[StateInterval] = []
    for i, (start, end, state, conf, reason) in enumerate(raw):
        # Clamp end to next interval's start so we never overlap.
        if i + 1 < len(raw):
            next_start = raw[i + 1][0]
            if next_start < end:
                end = next_start
        if end <= start:
            continue
        out.append(StateInterval(
            state=state, started_at=start, ended_at=end,
            confidence=conf, reason=reason,
        ))
    return out


def load_at_crossing_times(db: sqlite3.Connection) -> list[tuple[datetime, str]]:
    """Pull at-crossing observations from train_events (most reliable source —
    event='step' to a configured at-crossing berth means a train physically
    entered that section)."""
    rows = db.execute("""
        SELECT timestamp, headcode
        FROM train_events
        WHERE event = 'step' AND phase = 'at_crossing'
        ORDER BY timestamp
    """).fetchall()
    return [(parse_ts(ts), hc) for ts, hc in rows if ts and hc]


def derive_ground_truth(
    at_crossing: list[tuple[datetime, str]],
    pre_closure_secs: float,
    crossing_clearance_secs: float,
    post_clearance_secs: float,
) -> list[GroundTruthWindow]:
    """Expand each at-crossing observation into a closure window and merge
    overlaps. Window = [obs - pre_closure, obs + clearance + post]."""
    if not at_crossing:
        return []

    raw = []
    for ts, hc in at_crossing:
        raw.append(GroundTruthWindow(
            closed_from=ts - timedelta(seconds=pre_closure_secs),
            closed_until=ts + timedelta(seconds=crossing_clearance_secs + post_clearance_secs),
            sources=[hc],
        ))
    raw.sort(key=lambda w: w.closed_from)

    # Merge overlapping windows
    merged: list[GroundTruthWindow] = []
    cur = raw[0]
    for nxt in raw[1:]:
        if nxt.closed_from <= cur.closed_until:
            # Overlap — extend
            cur.closed_until = max(cur.closed_until, nxt.closed_until)
            cur.sources.extend(nxt.sources)
        else:
            merged.append(cur)
            cur = nxt
    merged.append(cur)
    return merged


# ---------------------------------------------------------------------------
# Confusion matrix construction
# ---------------------------------------------------------------------------


@dataclass
class CoverageResult:
    tp_secs: float = 0.0
    fp_secs: float = 0.0
    tn_secs: float = 0.0
    fn_secs: float = 0.0
    excluded_secs: float = 0.0
    fps: list[FPInterval] = field(default_factory=list)
    fns: list[FNInterval] = field(default_factory=list)

    @property
    def total_in_scope(self) -> float:
        return self.tp_secs + self.fp_secs + self.tn_secs + self.fn_secs

    def precision(self) -> float | None:
        denom = self.tp_secs + self.fp_secs
        return self.tp_secs / denom if denom > 0 else None

    def recall(self) -> float | None:
        denom = self.tp_secs + self.fn_secs
        return self.tp_secs / denom if denom > 0 else None

    def accuracy(self) -> float | None:
        return (self.tp_secs + self.tn_secs) / self.total_in_scope if self.total_in_scope > 0 else None

    def f1(self) -> float | None:
        p, r = self.precision(), self.recall()
        if p is None or r is None or (p + r) == 0:
            return None
        return 2 * p * r / (p + r)


def compute_coverage(
    intervals: list[StateInterval],
    ground_truth: list[GroundTruthWindow],
    predicted_closed_states: set[str],
    bucket_secs: float = 10.0,
) -> CoverageResult:
    """Walk the timeline in fixed-size buckets, classifying each bucket
    against both the predictor's state and derived ground truth.

    Bucket semantics: each bucket represents [t, t + bucket_secs); we
    classify it by sampling at the bucket midpoint. Small `bucket_secs`
    (e.g. 10s) keeps the error bound much smaller than typical state
    durations (minutes) so this is accurate enough for headline metrics.
    """
    if not intervals:
        return CoverageResult()

    start = intervals[0].started_at
    end = intervals[-1].ended_at

    # Build flat lookup arrays for fast bucket classification.
    # Sort once; each lookup advances pointers.
    intervals_sorted = sorted(intervals, key=lambda i: i.started_at)
    gt_sorted = sorted(ground_truth, key=lambda w: w.closed_from)

    # Sentinels for the merge walk
    interval_idx = 0
    gt_idx = 0
    n_intervals = len(intervals_sorted)
    n_gt = len(gt_sorted)

    result = CoverageResult()

    # Track contiguous FP/FN runs so we can report them as intervals
    cur_fp_start: datetime | None = None
    cur_fp_state: str | None = None
    cur_fp_reason: str | None = None
    cur_fn_start: datetime | None = None
    cur_fn_state: str | None = None
    cur_fn_sources: list[str] = []

    def flush_fp(end_t: datetime):
        nonlocal cur_fp_start, cur_fp_state, cur_fp_reason
        if cur_fp_start is not None:
            result.fps.append(FPInterval(
                start=cur_fp_start, end=end_t,
                state=cur_fp_state or "?", reason=cur_fp_reason,
            ))
            cur_fp_start = cur_fp_state = cur_fp_reason = None

    def flush_fn(end_t: datetime):
        nonlocal cur_fn_start, cur_fn_state, cur_fn_sources
        if cur_fn_start is not None:
            result.fns.append(FNInterval(
                start=cur_fn_start, end=end_t,
                state=cur_fn_state or "?",
                sources=list(set(cur_fn_sources)),
            ))
            cur_fn_start = cur_fn_state = None
            cur_fn_sources = []

    t = start
    bucket = timedelta(seconds=bucket_secs)
    while t < end:
        midpoint = t + bucket / 2

        # Find current state interval
        while interval_idx < n_intervals - 1 and intervals_sorted[interval_idx].ended_at <= midpoint:
            interval_idx += 1
        cur_interval = intervals_sorted[interval_idx]
        if not (cur_interval.started_at <= midpoint < cur_interval.ended_at):
            # Gap in coverage — treat as excluded
            result.excluded_secs += bucket_secs
            flush_fp(t); flush_fn(t)
            t += bucket
            continue

        # Excluded states (stale_data, unknown) don't contribute to TP/FP/etc
        if cur_interval.state in EXCLUDED_STATES:
            result.excluded_secs += bucket_secs
            flush_fp(t); flush_fn(t)
            t += bucket
            continue

        # Find current ground truth window (if any)
        while gt_idx < n_gt and gt_sorted[gt_idx].closed_until <= midpoint:
            gt_idx += 1
        actually_closed = (
            gt_idx < n_gt
            and gt_sorted[gt_idx].closed_from <= midpoint < gt_sorted[gt_idx].closed_until
        )
        gt_sources = list(gt_sorted[gt_idx].sources) if actually_closed else []

        predicted_closed = cur_interval.state in predicted_closed_states

        if predicted_closed and actually_closed:
            result.tp_secs += bucket_secs
            flush_fp(t); flush_fn(t)
        elif predicted_closed and not actually_closed:
            result.fp_secs += bucket_secs
            flush_fn(t)
            if cur_fp_start is None:
                cur_fp_start = t
                cur_fp_state = cur_interval.state
                cur_fp_reason = cur_interval.reason
        elif not predicted_closed and actually_closed:
            result.fn_secs += bucket_secs
            flush_fp(t)
            if cur_fn_start is None:
                cur_fn_start = t
                cur_fn_state = cur_interval.state
            cur_fn_sources.extend(gt_sources)
        else:  # both open
            result.tn_secs += bucket_secs
            flush_fp(t); flush_fn(t)

        t += bucket

    flush_fp(end); flush_fn(end)
    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def fmt_duration(secs: float) -> str:
    """Format seconds → '1d 4h 23m' / '4h 23m 17s' / '17s'."""
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    if secs < 86400:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    return f"{secs // 86400}d {(secs % 86400) // 3600}h"


def print_report(
    result: CoverageResult,
    ground_truth: list[GroundTruthWindow],
    intervals: list[StateInterval],
    predicted_closed_states: set[str],
    fp_show: int,
    fn_show: int,
):
    print("=" * 76)
    print("COVERAGE METRIC")
    print("=" * 76)
    if intervals:
        print(f"Period:           {intervals[0].started_at.isoformat()} → {intervals[-1].ended_at.isoformat()}")
    print(f"State intervals:  {len(intervals)}")
    print(f"GT closure windows (merged): {len(ground_truth)}")
    if ground_truth:
        gt_total = sum((w.closed_until - w.closed_from).total_seconds() for w in ground_truth)
        gt_avg = gt_total / len(ground_truth)
        print(f"GT closed time:   {fmt_duration(gt_total)}  (avg {fmt_duration(gt_avg)} per window)")
    print(f"Predicted-closed: {{{', '.join(sorted(predicted_closed_states))}}}")
    print()

    print(f"Total in scope:   {fmt_duration(result.total_in_scope)}")
    print(f"Excluded (stale_data/unknown): {fmt_duration(result.excluded_secs)}")
    print()

    print("Confusion matrix (time):")
    print()
    print(f"                            Reality")
    print(f"                       CLOSED          OPEN")
    print(f"    Pred CLOSED   [ TP {fmt_duration(result.tp_secs):>9} ]  [ FP {fmt_duration(result.fp_secs):>9} ]")
    print(f"         OPEN     [ FN {fmt_duration(result.fn_secs):>9} ]  [ TN {fmt_duration(result.tn_secs):>9} ]")
    print()

    p, r, a, f1 = result.precision(), result.recall(), result.accuracy(), result.f1()

    def pct(x): return f"{x*100:.2f}%" if x is not None else "—"
    print(f"  Precision  ({pct(p):>7}) — when we said CLOSED, how often barriers were actually down")
    print(f"  Recall     ({pct(r):>7}) — of actual closures, how much we caught")
    print(f"  Accuracy   ({pct(a):>7}) — overall correctness")
    print(f"  F1         ({pct(f1):>7}) — harmonic mean of precision and recall")
    print()

    if result.fps and fp_show > 0:
        result.fps.sort(key=lambda i: -i.duration_secs)
        print(f"--- Largest false-positive intervals (top {min(fp_show, len(result.fps))} of {len(result.fps)}) ---")
        print(f"  {'When':<19}  {'Dur':<8}  {'State':<18}  Reason")
        for fp in result.fps[:fp_show]:
            when = fp.start.strftime("%Y-%m-%d %H:%M:%S")
            reason = (fp.reason or "")[:80]
            print(f"  {when}  {fmt_duration(fp.duration_secs):<8}  {fp.state:<18}  {reason}")
        print()

    if result.fns and fn_show > 0:
        result.fns.sort(key=lambda i: -i.duration_secs)
        print(f"--- Largest false-negative intervals (top {min(fn_show, len(result.fns))} of {len(result.fns)}) ---")
        print(f"  {'When':<19}  {'Dur':<8}  {'Pred state':<18}  Trains in window")
        for fn in result.fns[:fn_show]:
            when = fn.start.strftime("%Y-%m-%d %H:%M:%S")
            srcs = ",".join(fn.sources[:5])
            if len(fn.sources) > 5:
                srcs += f",+{len(fn.sources) - 5}"
            print(f"  {when}  {fmt_duration(fn.duration_secs):<8}  {fn.state:<18}  {srcs}")
        print()

    # State-distribution sanity check
    state_secs: Counter = Counter()
    for it in intervals:
        state_secs[it.state] += (it.ended_at - it.started_at).total_seconds()
    total = sum(state_secs.values()) or 1
    print("State-time distribution (raw):")
    for state in sorted(state_secs, key=lambda s: -state_secs[s]):
        print(f"  {state:<22} {fmt_duration(state_secs[state]):>10}  ({state_secs[state]*100/total:5.1f}%)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", type=Path, help="Path to crossing.db (state_intervals + train_events)")
    ap.add_argument("--bucket-secs", type=float, default=10.0,
                    help="Bucket size in seconds (default 10)")
    ap.add_argument("--pre-closure", type=float, default=180.0,
                    help="Seconds before at-crossing that barriers are assumed down (default 180; matches config)")
    ap.add_argument("--crossing-clearance", type=float, default=10.0,
                    help="Seconds train physically takes to clear (default 10)")
    ap.add_argument("--post-clearance", type=float, default=5.0,
                    help="Seconds after train clears before barriers raise (default 5)")
    ap.add_argument("--strict", action="store_true",
                    help="Count only CLOSED_INFERRED as predicted-closed (excludes CLOSING_PREDICTED)")
    ap.add_argument("--since", type=str, default=None,
                    help="ISO timestamp; ignore all data before this point (e.g. '2026-05-08T09:18:00+00:00')")
    ap.add_argument("--until", type=str, default=None,
                    help="ISO timestamp; ignore all data after this point")
    ap.add_argument("--fp-show", type=int, default=15, help="Show top-N false-positive intervals")
    ap.add_argument("--fn-show", type=int, default=15, help="Show top-N false-negative intervals")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"error: {args.db} not found", file=sys.stderr)
        sys.exit(1)

    db = sqlite3.connect(str(args.db))
    now = datetime.now(timezone.utc)

    intervals = load_state_intervals(db, now=now)
    at_crossing = load_at_crossing_times(db)
    db.close()

    # Apply time filters if requested
    since_dt = parse_ts(args.since) if args.since else None
    until_dt = parse_ts(args.until) if args.until else None
    if since_dt or until_dt:
        intervals = [
            StateInterval(
                state=i.state,
                started_at=max(i.started_at, since_dt) if since_dt else i.started_at,
                ended_at=min(i.ended_at, until_dt) if until_dt else i.ended_at,
                confidence=i.confidence, reason=i.reason,
            )
            for i in intervals
            if (since_dt is None or i.ended_at > since_dt)
            and (until_dt is None or i.started_at < until_dt)
        ]
        intervals = [i for i in intervals if i.ended_at > i.started_at]
        at_crossing = [
            (ts, hc) for ts, hc in at_crossing
            if (since_dt is None or ts >= since_dt) and (until_dt is None or ts <= until_dt)
        ]

    if not intervals:
        print("error: no state_intervals in scope", file=sys.stderr)
        sys.exit(1)
    if not at_crossing:
        print("error: no at_crossing train events in scope", file=sys.stderr)
        sys.exit(1)

    ground_truth = derive_ground_truth(
        at_crossing,
        pre_closure_secs=args.pre_closure,
        crossing_clearance_secs=args.crossing_clearance,
        post_clearance_secs=args.post_clearance,
    )

    predicted_closed = (
        PREDICTED_CLOSED_STATES_STRICT if args.strict else PREDICTED_CLOSED_STATES
    )

    result = compute_coverage(
        intervals=intervals,
        ground_truth=ground_truth,
        predicted_closed_states=predicted_closed,
        bucket_secs=args.bucket_secs,
    )

    print_report(
        result=result,
        ground_truth=ground_truth,
        intervals=intervals,
        predicted_closed_states=predicted_closed,
        fp_show=args.fp_show,
        fn_show=args.fn_show,
    )


if __name__ == "__main__":
    main()
