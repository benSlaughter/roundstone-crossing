"""Tests for the camera↔predictor comparison matcher.

The matcher pairs camera-detected closure events against the predictor's
CLOSED_INFERRED / CLOSING_PREDICTED runs from the predictions DB.
Production lesson learned: greedy-by-start-time matching mis-pairs
camera events when a single long predictor span covers several
back-to-back trains. The matcher under test uses overlap matching with
a tolerance window, which counts the long span as matching every
camera event it covers.
"""

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("cv2")

from experiments.camera_analysis.compare import compare_to_predictions


WINDOW_START = datetime(2026, 5, 14, 8, 0, 0, tzinfo=timezone.utc)
WINDOW_END = WINDOW_START + timedelta(hours=2)


@pytest.fixture
def site_cfg():
    return {
        "valid_range": {
            "starts_at": WINDOW_START.isoformat(),
            "ends_at": WINDOW_END.isoformat(),
        },
    }


def _seed_predictions(db_path: Path, runs: list[tuple[datetime, datetime, str]]):
    """Write a synthetic predictions table with the given (start, end, state) runs.
    Between runs the predictor is in state 'open'. Predictor ticks every 2s."""
    db = sqlite3.connect(str(db_path))
    db.execute("""
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            state TEXT NOT NULL,
            confidence REAL,
            predicted_change_at TEXT
        )
    """)

    rows: list[tuple[str, str]] = []
    cursor = WINDOW_START
    for start, end, state in runs:
        # Pad with 'open' ticks up to start
        while cursor < start:
            rows.append((cursor.isoformat(), 'open'))
            cursor += timedelta(seconds=2)
        # Then the run itself
        while cursor < end:
            rows.append((cursor.isoformat(), state))
            cursor += timedelta(seconds=2)
    while cursor < WINDOW_END:
        rows.append((cursor.isoformat(), 'open'))
        cursor += timedelta(seconds=2)

    db.executemany(
        "INSERT INTO predictions (timestamp, state, confidence, predicted_change_at) VALUES (?, ?, 0.9, NULL)",
        rows,
    )
    db.commit()
    db.close()


def _camera_event(start: datetime, end: datetime) -> dict:
    return {
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "duration_secs": (end - start).total_seconds(),
    }


# ── Headline: long predictor span covers multiple camera events ───────

class TestOneToManyMatching:
    """The original bug: predictor stays in CLOSED_INFERRED across
    several back-to-back trains; camera detector sees them as separate
    events. Overlap matcher must count every camera event as matched."""

    def test_long_predictor_span_matches_all_camera_events(self, tmp_path, site_cfg):
        # Predictor closed continuously for 20 min
        runs = [(WINDOW_START + timedelta(minutes=10),
                 WINDOW_START + timedelta(minutes=30),
                 'closed_inferred')]
        db = tmp_path / "p.db"
        _seed_predictions(db, runs)

        # 3 camera events all within that span
        camera = [
            _camera_event(WINDOW_START + timedelta(minutes=11),
                          WINDOW_START + timedelta(minutes=14)),
            _camera_event(WINDOW_START + timedelta(minutes=18),
                          WINDOW_START + timedelta(minutes=22)),
            _camera_event(WINDOW_START + timedelta(minutes=26),
                          WINDOW_START + timedelta(minutes=29)),
        ]

        result = compare_to_predictions(camera, site_cfg, db_path=db)
        assert result["status"] == "ok"
        assert result["matched"] == 3, "all 3 camera events should match the one long predictor span"
        assert result["camera_only"] == 0
        # The predictor span was used (matched), so predictor_only stays 0
        assert result["predictor_only"] == 0


# ── Camera-only and predictor-only events ─────────────────────────────

class TestCameraOnly:

    def test_camera_event_with_no_overlapping_predictor_is_camera_only(self, tmp_path, site_cfg):
        runs = [(WINDOW_START + timedelta(minutes=5),
                 WINDOW_START + timedelta(minutes=8),
                 'closed_inferred')]
        db = tmp_path / "p.db"
        _seed_predictions(db, runs)

        # Camera event 30 min later — no overlap, no near-overlap
        camera = [
            _camera_event(WINDOW_START + timedelta(minutes=40),
                          WINDOW_START + timedelta(minutes=43)),
        ]
        result = compare_to_predictions(camera, site_cfg, db_path=db)
        assert result["matched"] == 0
        assert result["camera_only"] == 1
        assert result["predictor_only"] == 1


class TestPredictorOnly:

    def test_predictor_span_with_no_camera_event_is_predictor_only(self, tmp_path, site_cfg):
        runs = [(WINDOW_START + timedelta(minutes=10),
                 WINDOW_START + timedelta(minutes=15),
                 'closed_inferred')]
        db = tmp_path / "p.db"
        _seed_predictions(db, runs)

        # Camera event 30 min later
        camera = [
            _camera_event(WINDOW_START + timedelta(minutes=45),
                          WINDOW_START + timedelta(minutes=50)),
        ]
        result = compare_to_predictions(camera, site_cfg, db_path=db)
        assert result["predictor_only"] == 1
        assert result["camera_only"] == 1


# ── Tolerance window (60s pad either side) ────────────────────────────

class TestOverlapTolerance:

    def test_close_but_not_touching_within_tolerance_matches(self, tmp_path, site_cfg):
        # Predictor 10:00-10:05, camera 10:06-10:09 — gap of 60s exactly
        runs = [(WINDOW_START + timedelta(minutes=10),
                 WINDOW_START + timedelta(minutes=15),
                 'closed_inferred')]
        db = tmp_path / "p.db"
        _seed_predictions(db, runs)

        # Camera starts 30s after predictor ends → within 60s tolerance
        camera = [
            _camera_event(WINDOW_START + timedelta(minutes=15, seconds=30),
                          WINDOW_START + timedelta(minutes=18)),
        ]
        result = compare_to_predictions(camera, site_cfg, db_path=db)
        assert result["matched"] == 1

    def test_far_apart_does_not_match(self, tmp_path, site_cfg):
        # Predictor 10:00-10:05, camera 10:08-10:11 — gap 180s > 60s tolerance
        runs = [(WINDOW_START + timedelta(minutes=10),
                 WINDOW_START + timedelta(minutes=15),
                 'closed_inferred')]
        db = tmp_path / "p.db"
        _seed_predictions(db, runs)
        camera = [
            _camera_event(WINDOW_START + timedelta(minutes=18),
                          WINDOW_START + timedelta(minutes=20)),
        ]
        result = compare_to_predictions(camera, site_cfg, db_path=db)
        assert result["matched"] == 0


# ── Open-ended camera event (recording cut mid-closure) ───────────────

class TestOpenEnded:

    def test_camera_event_without_end_still_matches(self, tmp_path, site_cfg):
        runs = [(WINDOW_START + timedelta(minutes=10),
                 WINDOW_START + timedelta(minutes=15),
                 'closed_inferred')]
        db = tmp_path / "p.db"
        _seed_predictions(db, runs)
        camera = [{
            "started_at": (WINDOW_START + timedelta(minutes=12)).isoformat(),
            "ended_at": None,
            "duration_secs": None,
        }]
        result = compare_to_predictions(camera, site_cfg, db_path=db)
        assert result["matched"] == 1


# ── No predictor data / no camera events ──────────────────────────────

class TestEdgeCases:

    def test_no_predictor_data_in_window_is_reported(self, tmp_path, site_cfg):
        db = tmp_path / "p.db"
        # Empty predictions table
        sqlite3.connect(str(db)).execute("""
            CREATE TABLE predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                state TEXT NOT NULL,
                confidence REAL,
                predicted_change_at TEXT
            )
        """).connection.commit()

        camera = [
            _camera_event(WINDOW_START + timedelta(minutes=5),
                          WINDOW_START + timedelta(minutes=8)),
        ]
        result = compare_to_predictions(camera, site_cfg, db_path=db)
        assert result["status"] == "ok"
        assert result["predictor_closures"] == 0
        assert result["camera_only"] == 1
        assert result["matched"] == 0
        assert "no predictor data" in result.get("note", "")

    def test_no_camera_closures_returns_status_only(self, tmp_path, site_cfg):
        db = tmp_path / "p.db"
        _seed_predictions(db, [])
        result = compare_to_predictions([], site_cfg, db_path=db)
        assert result["status"] == "no camera closures to compare"

    def test_db_missing_returns_status(self, site_cfg, tmp_path):
        camera = [_camera_event(WINDOW_START + timedelta(minutes=5),
                                WINDOW_START + timedelta(minutes=8))]
        result = compare_to_predictions(
            camera, site_cfg, db_path=tmp_path / "does_not_exist.db",
        )
        assert "DB not found" in result["status"]


# ── Lead-time accuracy (timing diff column) ───────────────────────────

class TestLeadTime:

    def test_negative_lead_when_predictor_starts_before_camera(self, tmp_path, site_cfg):
        # Predictor starts at 10:00, camera at 10:01 → predictor was 60s early
        runs = [(WINDOW_START + timedelta(minutes=10),
                 WINDOW_START + timedelta(minutes=15),
                 'closed_inferred')]
        db = tmp_path / "p.db"
        _seed_predictions(db, runs)
        camera = [_camera_event(WINDOW_START + timedelta(minutes=11),
                                WINDOW_START + timedelta(minutes=13))]
        result = compare_to_predictions(camera, site_cfg, db_path=db)
        # lead_secs = predictor_start - camera_start = -60
        assert result["matches"][0]["lead_secs"] == -60

    def test_positive_lead_when_predictor_starts_after_camera(self, tmp_path, site_cfg):
        runs = [(WINDOW_START + timedelta(minutes=10),
                 WINDOW_START + timedelta(minutes=15),
                 'closed_inferred')]
        db = tmp_path / "p.db"
        _seed_predictions(db, runs)
        # Camera starts BEFORE predictor → predictor is late
        camera = [_camera_event(WINDOW_START + timedelta(minutes=8),
                                WINDOW_START + timedelta(minutes=14))]
        result = compare_to_predictions(camera, site_cfg, db_path=db)
        # lead_secs = predictor_start - camera_start = +120
        assert result["matches"][0]["lead_secs"] == 120


# ── Short predictor runs are filtered ─────────────────────────────────

class TestShortRunFilter:

    def test_predictor_run_under_30s_is_ignored(self, tmp_path, site_cfg):
        runs = [(WINDOW_START + timedelta(minutes=10),
                 WINDOW_START + timedelta(minutes=10, seconds=20),  # 20s, under threshold
                 'closing_predicted')]
        db = tmp_path / "p.db"
        _seed_predictions(db, runs)
        camera = [_camera_event(WINDOW_START + timedelta(minutes=10),
                                WINDOW_START + timedelta(minutes=12))]
        result = compare_to_predictions(camera, site_cfg, db_path=db)
        assert result["predictor_closures"] == 0
        assert result["camera_only"] == 1
