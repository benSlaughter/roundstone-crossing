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


# ── SF endpoints ─────────────────────────────────────────────────────

def test_sf_events_empty(client):
    resp = client.get("/sf")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"events": []}


def test_sf_events_with_data(history_db, tracker, inferrer):
    from freezegun import freeze_time
    with freeze_time("2025-06-15 10:00:00", tz_offset=0):
        history_db.record_sf_event("LA", "16", "43")
        history_db.record_sf_event("LA", "2F", "FF")

    from src.api import create_app
    app = create_app(tracker, inferrer, history_db)
    client = TestClient(app)
    resp = client.get("/sf")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["events"]) == 2
    addresses = {ev["address"] for ev in data["events"]}
    assert addresses == {"16", "2F"}
    for ev in data["events"]:
        assert "data_hex" in ev
        assert "data_bin" in ev
        assert "timestamp" in ev


def test_sf_summary_empty(client):
    resp = client.get("/sf/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"addresses": []}


# ── Predictions windows endpoint ─────────────────────────────────────

def test_predictions_windows_no_rtt(client):
    """Without RTT, returns empty windows with error."""
    resp = client.get("/predictions/windows")
    assert resp.status_code == 200
    data = resp.json()
    assert data["windows"] == []
    assert "error" in data


def test_predictions_windows_empty(tracker, inferrer, history_db):
    """With RTT but no upcoming services, returns empty windows."""
    mock_rtt = MagicMock()
    mock_rtt.get_upcoming.return_value = []
    app = create_app(tracker, inferrer, history_db, rtt_client=mock_rtt)
    client = TestClient(app)
    resp = client.get("/predictions/windows")
    assert resp.status_code == 200
    data = resp.json()
    assert data["windows"] == []
    assert data["current_closure"] is None
    assert "generated_at" in data


def test_predictions_windows_eastbound(tracker, inferrer, history_db):
    """Eastbound train at ANG produces a closure window."""
    mock_rtt = MagicMock()
    from datetime import datetime, timezone, timedelta
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    dep_iso = future.isoformat()
    mock_rtt.get_upcoming.side_effect = lambda crs, limit: [
        {
            "headcode": "1H23", "direction": "east",
            "departure_iso": dep_iso, "arrival_iso": None,
            "origin": "Littlehampton", "destination": "London Victoria",
        }
    ] if crs == "ANG" else []
    app = create_app(tracker, inferrer, history_db, rtt_client=mock_rtt)
    client = TestClient(app)
    resp = client.get("/predictions/windows")
    data = resp.json()
    assert len(data["windows"]) == 1
    w = data["windows"][0]
    assert len(w["trains"]) == 1
    assert w["trains"][0]["headcode"] == "1H23"
    assert w["trains"][0]["direction"] == "east"
    assert w["duration_secs"] > 0


def test_predictions_windows_westbound(tracker, inferrer, history_db):
    """Westbound train at GBS produces a closure window."""
    mock_rtt = MagicMock()
    from datetime import datetime, timezone, timedelta
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    dep_iso = future.isoformat()
    mock_rtt.get_upcoming.side_effect = lambda crs, limit: [
        {
            "headcode": "1N20", "direction": "west",
            "departure_iso": dep_iso, "arrival_iso": None,
            "origin": "Brighton", "destination": "Southampton Central",
        }
    ] if crs == "GBS" else []
    app = create_app(tracker, inferrer, history_db, rtt_client=mock_rtt)
    client = TestClient(app)
    resp = client.get("/predictions/windows")
    data = resp.json()
    assert len(data["windows"]) == 1
    assert data["windows"][0]["trains"][0]["headcode"] == "1N20"
    assert data["windows"][0]["trains"][0]["direction"] == "west"


def test_predictions_windows_filters_wrong_direction(tracker, inferrer, history_db):
    """Westbound at ANG (past crossing) and eastbound at GBS (past crossing) are excluded."""
    mock_rtt = MagicMock()
    from datetime import datetime, timezone, timedelta
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    dep_iso = future.isoformat()
    mock_rtt.get_upcoming.side_effect = lambda crs, limit: [
        {"headcode": "1X99", "direction": "west", "departure_iso": dep_iso,
         "arrival_iso": None, "origin": "A", "destination": "B"}
    ] if crs == "ANG" else [
        {"headcode": "1Y99", "direction": "east", "departure_iso": dep_iso,
         "arrival_iso": None, "origin": "C", "destination": "D"}
    ]
    app = create_app(tracker, inferrer, history_db, rtt_client=mock_rtt)
    client = TestClient(app)
    resp = client.get("/predictions/windows")
    data = resp.json()
    assert data["windows"] == []


def test_predictions_windows_merges_overlapping(tracker, inferrer, history_db):
    """Two trains close together get merged into one window."""
    mock_rtt = MagicMock()
    from datetime import datetime, timezone, timedelta
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    dep1 = future.isoformat()
    dep2 = (future + timedelta(minutes=1)).isoformat()
    mock_rtt.get_upcoming.side_effect = lambda crs, limit: [
        {"headcode": "1A01", "direction": "east", "departure_iso": dep1,
         "arrival_iso": None, "origin": "A", "destination": "B"},
        {"headcode": "1A02", "direction": "east", "departure_iso": dep2,
         "arrival_iso": None, "origin": "C", "destination": "D"},
    ] if crs == "ANG" else []
    app = create_app(tracker, inferrer, history_db, rtt_client=mock_rtt)
    client = TestClient(app)
    resp = client.get("/predictions/windows")
    data = resp.json()
    assert len(data["windows"]) == 1
    assert len(data["windows"][0]["trains"]) == 2


def test_predictions_windows_skips_no_departure(tracker, inferrer, history_db):
    """Services without departure_iso are excluded."""
    mock_rtt = MagicMock()
    mock_rtt.get_upcoming.side_effect = lambda crs, limit: [
        {"headcode": "1Z99", "direction": "east", "departure_iso": None,
         "arrival_iso": None, "origin": "A", "destination": "B"}
    ] if crs == "ANG" else []
    app = create_app(tracker, inferrer, history_db, rtt_client=mock_rtt)
    client = TestClient(app)
    resp = client.get("/predictions/windows")
    data = resp.json()
    assert data["windows"] == []


def test_sf_summary_with_data(history_db, tracker, inferrer):
    from freezegun import freeze_time
    with freeze_time("2025-06-15 10:00:00", tz_offset=0):
        history_db.record_sf_event("LA", "16", "43")
        history_db.record_sf_event("LA", "16", "44")
        history_db.record_sf_event("LA", "2F", "FF")

    from src.api import create_app
    app = create_app(tracker, inferrer, history_db)
    client = TestClient(app)
    resp = client.get("/sf/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["addresses"]) == 2
    addr_map = {a["address"]: a for a in data["addresses"]}
    assert addr_map["16"]["change_count"] == 2
    assert addr_map["2F"]["change_count"] == 1
    assert "first_seen" in addr_map["16"]
    assert "last_seen" in addr_map["16"]
