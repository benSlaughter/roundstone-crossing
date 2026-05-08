"""Tests for the coverage metric script (experiments/coverage_metric.py).

The script lives under experiments/ rather than src/, so we add experiments/
to sys.path before importing. These tests focus on the algorithmic core
(ground-truth derivation, confusion-matrix computation, interval
reconstruction) — the CLI/argparse layer is left for manual smoke.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "experiments"))

import coverage_metric as cm  # noqa: E402


T0 = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_interval(state: str, start_offset_s: float, end_offset_s: float | None,
                  reason: str | None = None) -> cm.StateInterval:
    return cm.StateInterval(
        state=state,
        started_at=T0 + timedelta(seconds=start_offset_s),
        ended_at=(T0 + timedelta(seconds=end_offset_s)) if end_offset_s is not None
                  else T0 + timedelta(seconds=10_000),
        confidence=None,
        reason=reason,
    )


# ── Ground-truth derivation ─────────────────────────────────────────────


class TestDeriveGroundTruth:

    def test_empty_input(self):
        assert cm.derive_ground_truth([], 180, 10, 5) == []

    def test_single_at_crossing_expands_to_window(self):
        windows = cm.derive_ground_truth(
            [(T0, "1A23")], pre_closure_secs=180, crossing_clearance_secs=10, post_clearance_secs=5,
        )
        assert len(windows) == 1
        w = windows[0]
        assert w.closed_from == T0 - timedelta(seconds=180)
        assert w.closed_until == T0 + timedelta(seconds=15)
        assert w.sources == ["1A23"]

    def test_overlapping_windows_merge(self):
        # Two trains 60s apart with 180s pre-closure → windows overlap
        windows = cm.derive_ground_truth(
            [(T0, "1A23"), (T0 + timedelta(seconds=60), "2B45")],
            pre_closure_secs=180, crossing_clearance_secs=10, post_clearance_secs=5,
        )
        assert len(windows) == 1
        w = windows[0]
        # Earlier from, later until
        assert w.closed_from == T0 - timedelta(seconds=180)
        assert w.closed_until == T0 + timedelta(seconds=75)
        assert sorted(w.sources) == ["1A23", "2B45"]

    def test_separated_windows_dont_merge(self):
        # Trains 1 hour apart — clearly distinct windows
        windows = cm.derive_ground_truth(
            [(T0, "1A23"), (T0 + timedelta(hours=1), "2B45")],
            pre_closure_secs=180, crossing_clearance_secs=10, post_clearance_secs=5,
        )
        assert len(windows) == 2

    def test_three_overlapping_collapse_to_one(self):
        windows = cm.derive_ground_truth(
            [(T0, "A"),
             (T0 + timedelta(seconds=60), "B"),
             (T0 + timedelta(seconds=120), "C")],
            pre_closure_secs=180, crossing_clearance_secs=10, post_clearance_secs=5,
        )
        assert len(windows) == 1
        assert sorted(windows[0].sources) == ["A", "B", "C"]


# ── Coverage computation ────────────────────────────────────────────────


class TestComputeCoverage:

    def test_perfect_match_no_fp_no_fn(self):
        """Predictor closed exactly when GT says closed → all TP, no FP/FN."""
        intervals = [
            make_interval("open", 0, 100),
            make_interval("closed_inferred", 100, 300),
            make_interval("open", 300, 500),
        ]
        # Train at t=200 with 100s pre/post buffer would mean GT closed [100, 210]
        # Use buffers that match the closed interval exactly: [100, 300]
        ground_truth = [cm.GroundTruthWindow(
            closed_from=T0 + timedelta(seconds=100),
            closed_until=T0 + timedelta(seconds=300),
        )]
        result = cm.compute_coverage(
            intervals, ground_truth,
            predicted_closed_states={"closed_inferred"},
            bucket_secs=10,
        )
        assert result.fp_secs == 0
        assert result.fn_secs == 0
        # 200s of TP within [100, 300]
        assert 180 <= result.tp_secs <= 220
        assert result.precision() == 1.0
        assert result.recall() == 1.0

    def test_false_positive_detected(self):
        """Predictor says CLOSED while GT says OPEN → FP recorded."""
        intervals = [
            make_interval("open", 0, 100),
            make_interval("closed_inferred", 100, 300, reason="phantom"),
            make_interval("open", 300, 500),
        ]
        ground_truth = []  # No actual closure
        result = cm.compute_coverage(
            intervals, ground_truth,
            predicted_closed_states={"closed_inferred"},
            bucket_secs=10,
        )
        assert result.tp_secs == 0
        assert result.fp_secs > 0
        assert result.fn_secs == 0
        assert result.precision() == 0.0  # no true positives
        assert len(result.fps) == 1
        assert result.fps[0].state == "closed_inferred"
        assert result.fps[0].reason == "phantom"

    def test_false_negative_detected(self):
        """Predictor says OPEN while GT says CLOSED → FN recorded."""
        intervals = [
            make_interval("open", 0, 500),
        ]
        ground_truth = [cm.GroundTruthWindow(
            closed_from=T0 + timedelta(seconds=100),
            closed_until=T0 + timedelta(seconds=300),
            sources=["1A23"],
        )]
        result = cm.compute_coverage(
            intervals, ground_truth,
            predicted_closed_states={"closed_inferred"},
            bucket_secs=10,
        )
        assert result.fp_secs == 0
        assert result.fn_secs > 0
        assert result.recall() == 0.0
        assert len(result.fns) == 1
        assert result.fns[0].sources == ["1A23"]

    def test_excluded_states_dont_count(self):
        """STALE_DATA and UNKNOWN time is excluded from all four buckets."""
        intervals = [
            make_interval("stale_data", 0, 200),
            make_interval("open", 200, 400),
        ]
        ground_truth = []
        result = cm.compute_coverage(
            intervals, ground_truth,
            predicted_closed_states={"closed_inferred"},
            bucket_secs=10,
        )
        assert result.excluded_secs > 0
        assert 180 <= result.excluded_secs <= 220
        # The 200s of "open" with no GT = TN
        assert result.tn_secs > 0
        assert result.fp_secs == 0
        assert result.fn_secs == 0

    def test_closing_predicted_counted_when_in_predicted_closed_set(self):
        intervals = [
            make_interval("closing_predicted", 0, 100),
        ]
        # GT also closed in this window
        ground_truth = [cm.GroundTruthWindow(
            closed_from=T0, closed_until=T0 + timedelta(seconds=100),
        )]
        # Non-strict: closing_predicted counts as predicted-closed
        result = cm.compute_coverage(
            intervals, ground_truth,
            predicted_closed_states=cm.PREDICTED_CLOSED_STATES,
            bucket_secs=10,
        )
        assert result.tp_secs > 0
        assert result.recall() == 1.0

        # Strict: closing_predicted does NOT count
        result_strict = cm.compute_coverage(
            intervals, ground_truth,
            predicted_closed_states=cm.PREDICTED_CLOSED_STATES_STRICT,
            bucket_secs=10,
        )
        assert result_strict.tp_secs == 0
        assert result_strict.fn_secs > 0  # predictor said "closing", GT said closed → FN in strict mode

    def test_metric_calculations(self):
        """End-to-end check that precision/recall/accuracy/F1 are computed correctly."""
        intervals = [
            make_interval("closed_inferred", 0, 100),    # 100s
            make_interval("closed_inferred", 100, 200),  # 100s (still closed)
            make_interval("open", 200, 400),             # 200s open
        ]
        # GT closed [50, 150] — 100s overlap with predictor-closed [0, 200]
        # First 50s of TP-window: predictor closed, GT open → FP
        # Last 100s of GT (150-200): both closed → TP
        # OR rather: [0,50] FP (closed pred, open GT), [50,150] TP, [150,200] FP, [200,400] TN
        ground_truth = [cm.GroundTruthWindow(
            closed_from=T0 + timedelta(seconds=50),
            closed_until=T0 + timedelta(seconds=150),
        )]
        result = cm.compute_coverage(
            intervals, ground_truth,
            predicted_closed_states={"closed_inferred"},
            bucket_secs=10,
        )
        # Approx: TP=100, FP=100, FN=0, TN=200
        assert 80 <= result.tp_secs <= 120
        assert 80 <= result.fp_secs <= 120
        assert result.fn_secs == 0
        assert 180 <= result.tn_secs <= 220

        p, r, a, f1 = result.precision(), result.recall(), result.accuracy(), result.f1()
        # Precision around 0.5, recall 1.0
        assert 0.4 <= p <= 0.6
        assert r == 1.0
        # Accuracy around 75% (300 correct / 400 total)
        assert 0.7 <= a <= 0.8
        # F1 around 0.67
        assert 0.6 <= f1 <= 0.7


# ── Edge cases for the loader (when fed real prod-style data) ──────────


class TestLoadStateIntervals:

    def test_overlapping_intervals_get_clamped(self, tmp_path):
        """Production data has unclosed intervals from app restarts. The loader
        must reconstruct a clean non-overlapping timeline by clamping each
        interval's end to the next interval's start."""
        import sqlite3
        db_path = tmp_path / "test.db"
        db = sqlite3.connect(str(db_path))
        db.execute("""
            CREATE TABLE state_intervals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT, confidence REAL,
                started_at TEXT, ended_at TEXT, duration_secs REAL,
                active_train_count INTEGER, notes TEXT, reason TEXT
            )
        """)
        # Three intervals: first unclosed (NULL ended_at), second starts before first
        # would have ended; third closes normally
        rows = [
            ("open",            "2026-05-01T10:00:00+00:00", None),
            ("closed_inferred", "2026-05-01T10:05:00+00:00", "2026-05-01T10:08:00+00:00"),
            ("open",            "2026-05-01T10:08:00+00:00", "2026-05-01T10:15:00+00:00"),
        ]
        for state, start, end in rows:
            db.execute(
                "INSERT INTO state_intervals (state, started_at, ended_at) VALUES (?, ?, ?)",
                (state, start, end),
            )
        db.commit()

        intervals = cm.load_state_intervals(db, now=datetime(2026, 5, 1, 11, 0, 0, tzinfo=timezone.utc))
        db.close()

        # Should be 3 intervals, no overlap
        assert len(intervals) == 3
        # First (unclosed) interval clamped to the second's start
        assert intervals[0].ended_at == intervals[1].started_at
        # Total time spanned should equal end-of-last - start-of-first
        total = sum((i.ended_at - i.started_at).total_seconds() for i in intervals)
        assert total == 15 * 60  # 15 minutes

    def test_zero_duration_intervals_dropped(self, tmp_path):
        import sqlite3
        db_path = tmp_path / "test.db"
        db = sqlite3.connect(str(db_path))
        db.execute("""
            CREATE TABLE state_intervals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT, confidence REAL,
                started_at TEXT, ended_at TEXT, duration_secs REAL,
                active_train_count INTEGER, notes TEXT, reason TEXT
            )
        """)
        # Two rows starting at the SAME timestamp — clamping should drop the first.
        for state, start, end in [
            ("open", "2026-05-01T10:00:00+00:00", "2026-05-01T10:05:00+00:00"),
            ("closed_inferred", "2026-05-01T10:00:00+00:00", "2026-05-01T10:01:00+00:00"),
        ]:
            db.execute("INSERT INTO state_intervals (state, started_at, ended_at) VALUES (?, ?, ?)",
                       (state, start, end))
        db.commit()
        intervals = cm.load_state_intervals(db, now=datetime(2026, 5, 1, 11, 0, 0, tzinfo=timezone.utc))
        db.close()
        # The first row gets clamped to the second's start (same timestamp) → dropped
        assert len(intervals) == 1
        assert intervals[0].state == "closed_inferred"
