"""Tests for RTTClient — token management, rate limiting, polling, and get_upcoming."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.rtt import RTTClient


@pytest.fixture
def rtt():
    """RTTClient with a fake refresh token."""
    with patch.dict("os.environ", {"RTT_TOKEN": "test-refresh-token"}):
        return RTTClient(stations=["ANG", "GBS"], poll_interval=10)


# ── _ensure_token ────────────────────────────────────────────────────

class TestEnsureToken:

    def test_successful_token_fetch(self, rtt):
        valid_until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"token": "access-123", "validUntil": valid_until}

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt._ensure_token()

        assert result is True
        assert rtt._access_token == "access-123"
        assert rtt._token_expires is not None

    def test_valid_token_not_refreshed(self, rtt):
        rtt._access_token = "existing-token"
        rtt._token_expires = datetime.now(timezone.utc) + timedelta(hours=1)

        with patch("src.rtt.requests.get") as mock_get:
            result = rtt._ensure_token()

        assert result is True
        mock_get.assert_not_called()

    def test_expired_token_refreshed(self, rtt):
        rtt._access_token = "old-token"
        rtt._token_expires = datetime.now(timezone.utc) + timedelta(seconds=30)

        valid_until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"token": "new-token", "validUntil": valid_until}

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt._ensure_token()

        assert result is True
        assert rtt._access_token == "new-token"

    def test_http_error_returns_false(self, rtt):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt._ensure_token()

        assert result is False

    def test_malformed_response(self, rtt):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}  # missing 'token' key

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt._ensure_token()

        assert result is False

    def test_rate_limited_sets_retry_after(self, rtt):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {"Retry-After": "30"}

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt._ensure_token()

        assert result is False
        assert rtt._retry_after is not None

    def test_no_valid_until_uses_default_expiry(self, rtt):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"token": "access-456"}

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt._ensure_token()

        assert result is True
        assert rtt._access_token == "access-456"
        assert rtt._token_expires is not None

    def test_network_error_returns_false(self, rtt):
        with patch("src.rtt.requests.get", side_effect=requests.exceptions.ConnectionError("fail")):
            result = rtt._ensure_token()

        assert result is False

    def test_timeout_returns_false(self, rtt):
        with patch("src.rtt.requests.get", side_effect=requests.exceptions.Timeout("timeout")):
            result = rtt._ensure_token()

        assert result is False


# ── _handle_rate_limit ───────────────────────────────────────────────

class TestHandleRateLimit:

    def test_sets_retry_after(self, rtt):
        mock_resp = MagicMock()
        mock_resp.headers = {"Retry-After": "45"}

        rtt._handle_rate_limit(mock_resp)

        assert rtt._retry_after is not None
        diff = (rtt._retry_after - datetime.now(timezone.utc)).total_seconds()
        assert 43 <= diff <= 46

    def test_default_retry_after(self, rtt):
        mock_resp = MagicMock()
        mock_resp.headers = {}

        rtt._handle_rate_limit(mock_resp)

        diff = (rtt._retry_after - datetime.now(timezone.utc)).total_seconds()
        assert 58 <= diff <= 61

    def test_rate_limit_info_when_active(self, rtt):
        rtt._retry_after = datetime.now(timezone.utc) + timedelta(seconds=30)
        info = rtt.rate_limit_info
        assert info["active"] is True
        assert 28 <= info["remaining_secs"] <= 31
        assert "until" in info

    def test_rate_limit_info_when_inactive(self, rtt):
        info = rtt.rate_limit_info
        assert info["active"] is False

    def test_rate_limit_info_when_expired(self, rtt):
        rtt._retry_after = datetime.now(timezone.utc) - timedelta(seconds=5)
        info = rtt.rate_limit_info
        assert info["active"] is False

    def test_backoff_prevents_polling(self, rtt):
        rtt._retry_after = datetime.now(timezone.utc) + timedelta(seconds=60)
        rtt._access_token = "valid"
        rtt._token_expires = datetime.now(timezone.utc) + timedelta(hours=1)

        with patch("src.rtt.requests.get") as mock_get:
            rtt._poll_all_stations()

        mock_get.assert_not_called()

    def test_expired_backoff_allows_polling(self, rtt):
        rtt._retry_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        rtt._access_token = "valid"
        rtt._token_expires = datetime.now(timezone.utc) + timedelta(hours=1)

        mock_resp = MagicMock()
        mock_resp.status_code = 204

        with patch("src.rtt.requests.get", return_value=mock_resp):
            rtt._running = True
            rtt._poll_all_stations()


# ── _poll_station ────────────────────────────────────────────────────

class TestPollStation:

    def test_successful_poll_calls_process_service(self, rtt):
        rtt._access_token = "token"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": [{"temporalData": {"status": "AT_PLATFORM"},
                          "scheduleMetadata": {"trainReportingIdentity": "1A23"}}],
        }

        callback = MagicMock()
        rtt.set_callback(callback)

        with patch("src.rtt.requests.get", return_value=mock_resp):
            rtt._poll_station("ANG")

        # _process_service should have been called (callback invoked if status is actionable)
        callback.assert_called_once()

    def test_http_error(self, rtt):
        rtt._access_token = "token"
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")

        with patch("src.rtt.requests.get", return_value=mock_resp):
            rtt._poll_station("ANG")  # should not raise

    def test_timeout(self, rtt):
        rtt._access_token = "token"

        with patch("src.rtt.requests.get", side_effect=requests.exceptions.Timeout("timeout")):
            rtt._poll_station("ANG")  # should not raise

    def test_204_no_services(self, rtt):
        rtt._access_token = "token"
        mock_resp = MagicMock()
        mock_resp.status_code = 204

        with patch("src.rtt.requests.get", return_value=mock_resp):
            rtt._poll_station("ANG")  # should not raise

    def test_rate_limited(self, rtt):
        rtt._access_token = "token"
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {"Retry-After": "30"}

        with patch("src.rtt.requests.get", return_value=mock_resp):
            rtt._poll_station("ANG")

        assert rtt._retry_after is not None

    def test_empty_services_list(self, rtt):
        rtt._access_token = "token"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": [],
        }
        callback = MagicMock()
        rtt.set_callback(callback)

        with patch("src.rtt.requests.get", return_value=mock_resp):
            rtt._poll_station("ANG")

        callback.assert_not_called()


# ── get_upcoming ─────────────────────────────────────────────────────

def _make_rtt_service(headcode="1A23", status="EXPECTED", platform="1",
                      dest="London Victoria", origin="Littlehampton",
                      sched_dep="2025-06-15T10:45:00", rt_dep=None,
                      sched_arr="2025-06-15T10:43:00", rt_arr=None,
                      display_as="CALL", operator="Southern"):
    """Build a realistic RTT service dict matching the actual API shape."""
    return {
        "temporalData": {
            "status": status,
            "displayAs": display_as,
            "platform": platform,
            "arrival": {
                "scheduleAdvertised": sched_arr,
                "realtimeForecast": rt_arr or "",
            },
            "departure": {
                "scheduleAdvertised": sched_dep,
                "realtimeForecast": rt_dep or "",
            },
        },
        "scheduleMetadata": {
            "trainReportingIdentity": headcode,
            "operator": {"name": operator},
        },
        "origin": [{"location": {"description": origin}}],
        "destination": [{"location": {"description": dest}}],
    }


class TestGetUpcoming:

    def _setup_token(self, rtt):
        rtt._access_token = "valid-token"
        rtt._token_expires = datetime.now(timezone.utc) + timedelta(hours=1)

    def test_successful_parse_multiple_services(self, rtt):
        self._setup_token(rtt)
        services = [
            _make_rtt_service(headcode="1A23", platform="1", dest="London Victoria"),
            _make_rtt_service(headcode="1B45", platform="2", dest="Southampton Central"),
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": services,
        }

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG", limit=5)

        assert len(result) == 2
        assert result[0]["headcode"] == "1A23"
        assert result[1]["headcode"] == "1B45"
        assert result[0]["station"] == "Angmering"

    def test_empty_services(self, rtt):
        self._setup_token(rtt)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": [],
        }

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG")

        assert result == []

    def test_skips_departed_trains(self, rtt):
        self._setup_token(rtt)
        services = [
            _make_rtt_service(headcode="1X00", status="HAS_DEPTED"),
            _make_rtt_service(headcode="1A23", status="EXPECTED"),
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": services,
        }

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG")

        assert len(result) == 1
        assert result[0]["headcode"] == "1A23"

    def test_direction_from_platform_1(self, rtt):
        self._setup_token(rtt)
        svc = _make_rtt_service(platform="1", dest="Brighton")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": [svc],
        }

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG")

        assert result[0]["direction"] == "east"

    def test_direction_from_platform_2(self, rtt):
        self._setup_token(rtt)
        svc = _make_rtt_service(platform="2", dest="Southampton")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": [svc],
        }

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG")

        assert result[0]["direction"] == "west"

    def test_direction_inferred_from_east_destination(self, rtt):
        self._setup_token(rtt)
        svc = _make_rtt_service(platform="?", dest="London Victoria")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": [svc],
        }

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG")

        assert result[0]["direction"] == "east"

    def test_direction_inferred_from_west_destination(self, rtt):
        self._setup_token(rtt)
        svc = _make_rtt_service(platform="?", dest="Portsmouth Harbour")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": [svc],
        }

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG")

        assert result[0]["direction"] == "west"

    def test_direction_unknown(self, rtt):
        self._setup_token(rtt)
        svc = _make_rtt_service(platform="?", dest="Timbuktu")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": [svc],
        }

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG")

        assert result[0]["direction"] == ""

    def test_arrival_departure_iso_extraction(self, rtt):
        self._setup_token(rtt)
        svc = _make_rtt_service(
            sched_dep="2025-06-15T10:45:00",
            sched_arr="2025-06-15T10:43:00",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": [svc],
        }

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG")

        assert result[0]["departure"] == "10:45"
        assert result[0]["arrival"] == "10:43"
        assert result[0]["departure_iso"] == "2025-06-15T10:45:00"
        assert result[0]["arrival_iso"] == "2025-06-15T10:43:00"

    def test_delayed_train_shows_scheduled(self, rtt):
        self._setup_token(rtt)
        svc = _make_rtt_service(
            sched_dep="2025-06-15T10:45:00",
            rt_dep="2025-06-15T10:52:00",
            sched_arr="2025-06-15T10:43:00",
            rt_arr="2025-06-15T10:50:00",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": [svc],
        }

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG")

        r = result[0]
        # departure shows realtime
        assert r["departure"] == "10:52"
        assert r["departure_scheduled"] == "10:45"
        assert r["departure_iso"] == "2025-06-15T10:52:00"
        # arrival shows realtime
        assert r["arrival"] == "10:50"
        assert r["arrival_scheduled"] == "10:43"

    def test_limit_respected(self, rtt):
        self._setup_token(rtt)
        services = [_make_rtt_service(headcode=f"1A{i:02d}") for i in range(10)]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": services,
        }

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG", limit=3)

        assert len(result) == 3

    def test_no_token_returns_empty(self, rtt):
        rtt._access_token = None
        rtt._token_expires = None
        rtt._refresh_token = ""

        result = rtt.get_upcoming("ANG")
        assert result == []

    def test_non_200_returns_empty(self, rtt):
        self._setup_token(rtt)
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG")

        assert result == []

    def test_network_error_returns_empty(self, rtt):
        self._setup_token(rtt)

        with patch("src.rtt.requests.get", side_effect=requests.exceptions.ConnectionError("fail")):
            result = rtt.get_upcoming("ANG")

        assert result == []

    def test_malformed_json_returns_empty(self, rtt):
        self._setup_token(rtt)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("bad json")

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG")

        assert result == []

    def test_missing_fields_in_service(self, rtt):
        self._setup_token(rtt)
        # Minimal service with missing fields
        svc = {
            "temporalData": {"status": "EXPECTED"},
            "scheduleMetadata": {},
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {"location": {"description": "Angmering"}},
            "services": [svc],
        }

        with patch("src.rtt.requests.get", return_value=mock_resp):
            result = rtt.get_upcoming("ANG")

        assert len(result) == 1
        assert result[0]["headcode"] == "?"
        assert result[0]["origin"] == ""
        assert result[0]["destination"] == ""


# ── _process_service ─────────────────────────────────────────────────

class TestProcessService:

    def test_no_callback_skips(self, rtt):
        rtt._on_update = None
        svc = {"temporalData": {"status": "AT_PLATFORM"},
               "scheduleMetadata": {"trainReportingIdentity": "1A23"}}
        rtt._process_service(svc, "Angmering")  # should not raise

    def test_non_actionable_status_skipped(self, rtt):
        callback = MagicMock()
        rtt.set_callback(callback)
        svc = {"temporalData": {"status": "EXPECTED"},
               "scheduleMetadata": {"trainReportingIdentity": "1A23"}}
        rtt._process_service(svc, "Angmering")
        callback.assert_not_called()

    def test_no_headcode_skipped(self, rtt):
        callback = MagicMock()
        rtt.set_callback(callback)
        svc = {"temporalData": {"status": "AT_PLATFORM"},
               "scheduleMetadata": {}}
        rtt._process_service(svc, "Angmering")
        callback.assert_not_called()

    def test_not_passenger_service_skipped(self, rtt):
        callback = MagicMock()
        rtt.set_callback(callback)
        svc = {"temporalData": {"status": "AT_PLATFORM"},
               "scheduleMetadata": {"trainReportingIdentity": "0Z99",
                                    "inPassengerService": False}}
        rtt._process_service(svc, "Angmering")
        callback.assert_not_called()

    def test_actionable_status_calls_callback(self, rtt):
        callback = MagicMock()
        rtt.set_callback(callback)
        svc = {
            "temporalData": {"status": "AT_PLATFORM", "displayAs": "CALL"},
            "scheduleMetadata": {"trainReportingIdentity": "1A23",
                                 "inPassengerService": True},
            "locationMetadata": {"platform": {"actual": "1", "planned": "2"}},
            "origin": [{"location": {"longCodes": ["LTHMPT"]}}],
            "destination": [{"location": {"longCodes": ["VIC"]}}],
        }
        rtt._process_service(svc, "Angmering")
        callback.assert_called_once()
        kwargs = callback.call_args[1]
        assert kwargs["headcode"] == "1A23"
        assert kwargs["station"] == "Angmering"
        assert kwargs["platform"] == "1"
        assert kwargs["status"] == "AT_PLATFORM"


# ── Constructor / start / stop ───────────────────────────────────────

class TestRTTClientInit:

    def test_no_token_disables(self):
        with patch.dict("os.environ", {}, clear=True):
            client = RTTClient(stations=["ANG"])
        assert client._refresh_token == ""

    def test_start_without_token_noop(self):
        with patch.dict("os.environ", {}, clear=True):
            client = RTTClient(stations=["ANG"])
        client.start()
        assert client._thread is None

    def test_stop_sets_flag(self, rtt):
        rtt._running = True
        rtt.stop()
        assert rtt._running is False
