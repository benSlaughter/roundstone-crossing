"""Tests for the API endpoints."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

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


# ── Feedback endpoints ───────────────────────────────────────────────

class TestFeedbackPost:

    def test_valid_message(self, tracker, inferrer, history_db):
        app = create_app(tracker, inferrer, history_db)
        client = TestClient(app)
        resp = client.post("/feedback", json={"message": "Great app!"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "id" in data

    def test_empty_message_rejected(self, tracker, inferrer, history_db):
        app = create_app(tracker, inferrer, history_db)
        client = TestClient(app)
        resp = client.post("/feedback", json={"message": ""})
        assert resp.status_code == 422

    def test_message_too_long_rejected(self, tracker, inferrer, history_db):
        app = create_app(tracker, inferrer, history_db)
        client = TestClient(app)
        resp = client.post("/feedback", json={"message": "x" * 2001})
        assert resp.status_code == 422

    def test_missing_message_field(self, tracker, inferrer, history_db):
        app = create_app(tracker, inferrer, history_db)
        client = TestClient(app)
        resp = client.post("/feedback", json={})
        assert resp.status_code == 422


class TestFeedbackGet:

    def test_no_token_returns_401(self, tracker, inferrer, history_db):
        with patch.dict("os.environ", {"ADMIN_TOKEN": "secret123"}):
            app = create_app(tracker, inferrer, history_db)
        client = TestClient(app)
        resp = client.get("/feedback")
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self, tracker, inferrer, history_db):
        with patch.dict("os.environ", {"ADMIN_TOKEN": "secret123"}):
            app = create_app(tracker, inferrer, history_db)
        client = TestClient(app)
        resp = client.get("/feedback", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_correct_token_returns_200(self, tracker, inferrer, history_db):
        with patch.dict("os.environ", {"ADMIN_TOKEN": "secret123"}):
            app = create_app(tracker, inferrer, history_db)
        client = TestClient(app)
        resp = client.get("/feedback", headers={"Authorization": "Bearer secret123"})
        assert resp.status_code == 200
        data = resp.json()
        assert "feedback" in data
        assert isinstance(data["feedback"], list)

    def test_correct_token_returns_submitted_feedback(self, tracker, inferrer, history_db):
        with patch.dict("os.environ", {"ADMIN_TOKEN": "mytoken"}):
            app = create_app(tracker, inferrer, history_db)
        client = TestClient(app)

        # Submit feedback first
        client.post("/feedback", json={"message": "Test feedback"})

        # Retrieve it
        resp = client.get("/feedback", headers={"Authorization": "Bearer mytoken"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["feedback"]) >= 1
        assert any(f["message"] == "Test feedback" for f in data["feedback"])

    def test_no_admin_token_configured_returns_503(self, tracker, inferrer, history_db):
        with patch.dict("os.environ", {"ADMIN_TOKEN": ""}, clear=False):
            app = create_app(tracker, inferrer, history_db)
        client = TestClient(app)
        resp = client.get("/feedback", headers={"Authorization": "Bearer anything"})
        assert resp.status_code == 503


# ── Predictions/next endpoint ────────────────────────────────────────

class TestPredictionsNext:

    def test_no_rtt_returns_current_state(self, client):
        """Without RTT and no active trains, returns empty events."""
        resp = client.get("/predictions/next")
        assert resp.status_code == 200
        data = resp.json()
        assert "current_state" in data
        assert "events" in data
        assert "generated_at" in data
        assert isinstance(data["events"], list)

    def test_with_schedule_events(self, tracker, inferrer, history_db):
        """RTT data produces closing/opening event pairs."""
        mock_rtt = MagicMock()
        from datetime import datetime, timezone, timedelta
        future = datetime.now(timezone.utc) + timedelta(minutes=15)
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
        resp = client.get("/predictions/next")
        data = resp.json()
        assert data["current_state"] == "unknown"
        events = data["events"]
        assert len(events) >= 2
        assert events[0]["event"] == "closing"
        assert events[1]["event"] == "opening"
        assert events[0]["source"] == "schedule"
        assert events[0]["in_seconds"] > 0
        assert "in_human" in events[0]
        assert len(events[0]["trains"]) == 1
        assert events[0]["trains"][0]["headcode"] == "1H23"
        assert events[0]["trains"][0]["direction"] == "east"

    def test_live_closure_produces_events(self, tracker, inferrer, history_db):
        """When crossing is closed, live opening event is returned."""
        from datetime import datetime, timezone, timedelta
        from src.models import CrossingState

        inferrer.status.state = CrossingState.CLOSED_INFERRED
        inferrer.status.confidence = 0.9
        inferrer.status.since = datetime.now(timezone.utc) - timedelta(seconds=30)
        inferrer.status.predicted_change = datetime.now(timezone.utc) + timedelta(seconds=60)
        inferrer.status.predicted_next_state = CrossingState.OPENING_PREDICTED
        inferrer.status.active_trains = [
            TrackedTrain(headcode="2B45", direction=Direction.DOWN,
                         phase=TrainPhase.AT_CROSSING),
        ]

        app = create_app(tracker, inferrer, history_db)
        client = TestClient(app)
        resp = client.get("/predictions/next")
        data = resp.json()
        assert data["current_state"] == "closed_inferred"
        events = data["events"]
        # Should have at least an opening event
        opening_events = [e for e in events if e["event"] == "opening"]
        assert len(opening_events) >= 1
        assert opening_events[0]["source"] == "live"
        assert opening_events[0]["in_seconds"] > 0

    def test_limit_parameter(self, tracker, inferrer, history_db):
        """Limit parameter restricts number of events."""
        mock_rtt = MagicMock()
        from datetime import datetime, timezone, timedelta
        # Create multiple future trains
        services = []
        for i in range(5):
            future = datetime.now(timezone.utc) + timedelta(minutes=15 + i * 30)
            services.append({
                "headcode": f"1A0{i}", "direction": "east",
                "departure_iso": future.isoformat(), "arrival_iso": None,
                "origin": "A", "destination": "B",
            })
        mock_rtt.get_upcoming.side_effect = lambda crs, limit: services if crs == "ANG" else []
        app = create_app(tracker, inferrer, history_db, rtt_client=mock_rtt)
        client = TestClient(app)
        resp = client.get("/predictions/next?limit=2")
        data = resp.json()
        assert len(data["events"]) == 2

    def test_directions_normalized(self, tracker, inferrer, history_db):
        """Directions are normalized to east/west in output."""
        from datetime import datetime, timezone, timedelta
        from src.models import CrossingState

        inferrer.status.state = CrossingState.CLOSING_PREDICTED
        inferrer.status.confidence = 0.8
        inferrer.status.since = datetime.now(timezone.utc)
        inferrer.status.predicted_change = datetime.now(timezone.utc) + timedelta(seconds=30)
        inferrer.status.predicted_next_state = CrossingState.CLOSED_INFERRED
        inferrer.status.active_trains = [
            TrackedTrain(headcode="1X99", direction=Direction.UP,
                         phase=TrainPhase.STRIKE_IN),
        ]

        app = create_app(tracker, inferrer, history_db)
        client = TestClient(app)
        resp = client.get("/predictions/next")
        data = resp.json()
        events = data["events"]
        closing_events = [e for e in events if e["event"] == "closing"]
        assert len(closing_events) >= 1
        for t in closing_events[0]["trains"]:
            assert t["direction"] in ("east", "west", None)

    def test_human_formatting(self, tracker, inferrer, history_db):
        """in_human field provides readable text."""
        mock_rtt = MagicMock()
        from datetime import datetime, timezone, timedelta
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        mock_rtt.get_upcoming.side_effect = lambda crs, limit: [
            {
                "headcode": "1A23", "direction": "east",
                "departure_iso": future.isoformat(), "arrival_iso": None,
                "origin": "A", "destination": "B",
            }
        ] if crs == "ANG" else []
        app = create_app(tracker, inferrer, history_db, rtt_client=mock_rtt)
        client = TestClient(app)
        resp = client.get("/predictions/next")
        data = resp.json()
        for event in data["events"]:
            assert isinstance(event["in_human"], str)
            assert event["in_human"] != ""


# ── Health endpoint: warnings and RTT status ─────────────────────────

def test_health_includes_warnings_and_rtt(client):
    """Health response has warnings array and rtt section."""
    resp = client.get("/health")
    data = resp.json()
    assert "warnings" in data
    assert isinstance(data["warnings"], list)
    assert "rtt" in data
    assert data["rtt"]["available"] is False  # no rtt_client in default fixture


def test_health_no_feed_has_warning(client):
    """When no feed messages received, warnings includes feed message."""
    data = client.get("/health").json()
    assert any("feed" in w.lower() or "no live data" in w.lower() for w in data["warnings"])


def test_health_with_rtt_rate_limited(tracker, inferrer, history_db):
    """When RTT is rate-limited, health shows warning and rtt.rate_limited=True."""
    from datetime import datetime, timezone, timedelta
    mock_rtt = MagicMock()
    mock_rtt.rate_limit_info = {
        "active": True,
        "until": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
        "remaining_secs": 30,
    }
    app = create_app(tracker, inferrer, history_db, rtt_client=mock_rtt)
    client = TestClient(app)
    data = client.get("/health").json()
    assert data["rtt"]["rate_limited"] is True
    assert data["rtt"]["rate_limited_remaining_secs"] == 30
    assert data["status"] == "degraded"
    assert any("rate-limited" in w for w in data["warnings"])


def test_health_with_rtt_not_rate_limited(tracker, inferrer, history_db):
    """When RTT is healthy, no rate-limit warning."""
    mock_rtt = MagicMock()
    mock_rtt.rate_limit_info = {"active": False}
    app = create_app(tracker, inferrer, history_db, rtt_client=mock_rtt)
    client = TestClient(app)
    data = client.get("/health").json()
    assert data["rtt"]["rate_limited"] is False
    assert not any("rate-limited" in w for w in data["warnings"])
