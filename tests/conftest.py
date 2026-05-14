"""Shared test fixtures for Roundstone Crossing Predictor tests."""

import pytest
from datetime import datetime, timezone

from src.models import TrackedTrain, TrainPhase, Direction, CrossingState, CrossingStatus
from src.tracker import TrainTracker
from src.inferrer import CrossingInferrer
from src.history import HistoryLogger


@pytest.fixture
def test_config():
    """Minimal synthetic config for testing — not tied to production berth IDs."""
    return {
        "td": {
            "area_id": "LA",
            "approach_berths": {"up": ["0042"], "down": ["0033"]},
            "strike_in_berths": {"up": ["0040", "0038"], "down": ["0035", "0037", "0039"]},
            "at_crossing_berths": {"up": ["0036"], "down": ["0041"]},
            "clear_berths": {"up": ["0034"], "down": ["A027"]},
        },
        "station_berths": {
            "0038": {"station": "Angmering", "direction": "up", "entry": 8, "platform": 17},
            "0041": {"station": "Angmering", "direction": "down", "entry": 25, "platform": 17},
            "0035": {"station": "Goring", "direction": "down", "entry": 93, "platform": 83},
        },
        "timing": {
            "pre_closure_secs": 120,
            "post_clearance_secs": 5,
            "crossing_clearance_secs": 10,
            "min_warning_secs": 27,
            "stale_threshold_secs": 300,
        },
        # Inference toggles. The inferrer no longer reads `use_routes` —
        # routes are unconditionally ignored for prediction. See
        # src/inferrer.py docstring.
        "inference": {},
        "trust": {
            "timing_points": [
                {"tiploc": "ANGMRNG", "stanox": "87998", "direction": "up",
                 "event": "departure", "action": "predict", "offset_secs": 90},
                {"tiploc": "GORNGBS", "stanox": "87997", "direction": "down",
                 "event": "departure", "action": "predict", "offset_secs": 120},
                {"tiploc": "ANGMRNG", "stanox": "87998", "direction": "down",
                 "event": "arrival", "action": "clear", "station": "Angmering"},
                {"tiploc": "GORNGBS", "stanox": "87997", "direction": "up",
                 "event": "arrival", "action": "clear", "station": "Goring"},
                {"tiploc": "ANGMRNG", "stanox": "87998", "direction": "up",
                 "event": "arrival", "action": "at_station", "station": "Angmering"},
                {"tiploc": "GORNGBS", "stanox": "87997", "direction": "down",
                 "event": "arrival", "action": "at_station", "station": "Goring"},
            ],
        },
    }


@pytest.fixture
def tracker(test_config):
    """A TrainTracker with the test config."""
    return TrainTracker(test_config)


@pytest.fixture
def inferrer(test_config):
    """A CrossingInferrer with the test config."""
    return CrossingInferrer(test_config)


@pytest.fixture
def history_db(tmp_path):
    """A HistoryLogger using a temp database file."""
    return HistoryLogger(db_path=tmp_path / "test.db")


@pytest.fixture
def now():
    """A fixed 'now' datetime for deterministic tests."""
    return datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def make_train():
    """Factory fixture for creating TrackedTrain instances."""
    def _make(headcode="1A23", direction=Direction.UP, phase=TrainPhase.APPROACHING,
              confidence=0.5, last_berth=None, **kwargs):
        return TrackedTrain(
            headcode=headcode, direction=direction, phase=phase,
            confidence=confidence, last_berth=last_berth, **kwargs,
        )
    return _make
