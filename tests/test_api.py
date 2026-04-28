"""Tests for the API endpoints."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from src.api import create_app
from src.models import TrackedTrain, Direction, TrainPhase


@pytest.fixture
def client(tracker, inferrer, history_db):
    app = create_app(tracker, inferrer, history_db)
    return TestClient(app)


def test_status(client):
    resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("state", "confidence", "since", "active_trains"):
        assert key in data


def test_diagram_empty(client):
    resp = client.get("/diagram")
    assert resp.status_code == 200
    data = resp.json()
    assert "state" in data
    assert "confidence" in data
    assert data["trains"] == []


def test_diagram_with_train(tracker, inferrer, history_db):
    with tracker._lock:
        tracker.trains["1A23"] = TrackedTrain(
            headcode="1A23", direction=Direction.UP, phase=TrainPhase.APPROACHING,
        )
    app = create_app(tracker, inferrer, history_db)
    client = TestClient(app)
    resp = client.get("/diagram")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["trains"]) == 1
    assert data["trains"][0]["headcode"] == "1A23"
    assert data["trains"][0]["direction"] == "up"
    assert data["trains"][0]["phase"] == "approaching"


def test_predictions(client):
    resp = client.get("/predictions")
    assert resp.status_code == 200
    data = resp.json()
    assert "crossing_state" in data
    assert "trains" in data
    assert isinstance(data["trains"], list)


def test_history_intervals(client):
    resp = client.get("/history?type=intervals")
    assert resp.status_code == 200
    data = resp.json()
    assert "intervals" in data


def test_history_passages(client):
    resp = client.get("/history?type=passages")
    assert resp.status_code == 200
    data = resp.json()
    assert "passages" in data


def test_stats(client):
    resp = client.get("/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_intervals" in data
    assert "total_passages" in data


def test_next_without_rtt(client):
    resp = client.get("/next?station=ANG&limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data


def test_next_with_rtt(tracker, inferrer, history_db):
    mock_rtt = MagicMock()
    mock_rtt.get_upcoming.return_value = [
        {"service_uid": "W12345", "headcode": "1A99", "departure": "10:45"},
    ]
    app = create_app(tracker, inferrer, history_db, rtt_client=mock_rtt)
    client = TestClient(app)
    resp = client.get("/next?station=ANG&limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert "services" in data
    assert len(data["services"]) == 1
    mock_rtt.get_upcoming.assert_called_once_with("ANG", 5)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("healthy", "degraded")
    assert "uptime_secs" in data
    assert "started_at" in data
    assert "feed" in data
    assert "trains" in data
    assert "db_size_mb" in data
    assert isinstance(data["trains"]["active"], int)
    assert isinstance(data["trains"]["total_tracked"], int)


def test_health_degraded_without_feed(client):
    resp = client.get("/health")
    data = resp.json()
    # No feed messages have been received, so status should be degraded
    assert data["status"] == "degraded"
    assert data["feed"]["last_message"] is None
