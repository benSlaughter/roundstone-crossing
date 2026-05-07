"""Tests for NRODListener message parsing and dispatch."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from freezegun import freeze_time

from src.feed import NRODListener


@pytest.fixture
def mock_tracker():
    tracker = MagicMock()
    tracker.area_id = "LA"
    return tracker


@pytest.fixture
def listener(mock_tracker):
    return NRODListener(mock_tracker)


# ── _parse_td_time ──────────────────────────────────────────────────

class TestParseTdTime:

    @freeze_time("2025-06-15 10:00:00", tz_offset=0)
    def test_normal_hhmm(self):
        result = NRODListener._parse_td_time("1030")
        assert result == datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)

    @freeze_time("2025-06-15 00:05:00", tz_offset=0)
    def test_midnight_rollover(self):
        result = NRODListener._parse_td_time("2355")
        assert result == datetime(2025, 6, 14, 23, 55, 0, tzinfo=timezone.utc)

    @freeze_time("2025-06-15 10:00:00", tz_offset=0)
    def test_invalid_empty_string(self):
        result = NRODListener._parse_td_time("")
        assert result == datetime(2025, 6, 15, 10, 0, 0, tzinfo=timezone.utc)

    @freeze_time("2025-06-15 08:00:00", tz_offset=0)
    def test_midnight_zero(self):
        result = NRODListener._parse_td_time("0000")
        assert result == datetime(2025, 6, 15, 0, 0, 0, tzinfo=timezone.utc)


# ── _handle_td ───────────────────────────────────────────────────────

class TestHandleTd:

    def test_ca_msg_correct_area(self, listener, mock_tracker):
        messages = [{"CA_MSG": {
            "area_id": "LA", "from": "A001", "to": "A002",
            "descr": "1A23", "time": "1030",
        }}]
        listener._handle_td(messages)
        mock_tracker.handle_td_step.assert_called_once()
        args = mock_tracker.handle_td_step.call_args
        assert args[0][0] == "A001"
        assert args[0][1] == "A002"
        assert args[0][2] == "1A23"

    def test_cb_msg_correct_area(self, listener, mock_tracker):
        messages = [{"CB_MSG": {
            "area_id": "LA", "from": "A001",
            "descr": "1A23", "time": "1030",
        }}]
        listener._handle_td(messages)
        mock_tracker.handle_td_cancel.assert_called_once()
        args = mock_tracker.handle_td_cancel.call_args
        assert args[0][0] == "A001"
        assert args[0][1] == "1A23"

    def test_cc_msg_correct_area(self, listener, mock_tracker):
        messages = [{"CC_MSG": {
            "area_id": "LA", "to": "A002",
            "descr": "1A23", "time": "1030",
        }}]
        listener._handle_td(messages)
        mock_tracker.handle_td_step.assert_called_once()
        args = mock_tracker.handle_td_step.call_args
        assert args[0][0] == ""
        assert args[0][1] == "A002"
        assert args[0][2] == "1A23"

    def test_wrong_area_ignored(self, listener, mock_tracker):
        messages = [{"CA_MSG": {
            "area_id": "WX", "from": "A001", "to": "A002",
            "descr": "1A23", "time": "1030",
        }}]
        listener._handle_td(messages)
        mock_tracker.handle_td_step.assert_not_called()
        mock_tracker.handle_td_cancel.assert_not_called()

    def test_missing_headcode_ignored(self, listener, mock_tracker):
        messages = [{"CA_MSG": {
            "area_id": "LA", "from": "A001", "to": "A002",
            "descr": "", "time": "1030",
        }}]
        listener._handle_td(messages)
        mock_tracker.handle_td_step.assert_not_called()

    def test_ca_missing_to_berth_ignored(self, listener, mock_tracker):
        messages = [{"CA_MSG": {
            "area_id": "LA", "from": "A001", "to": "",
            "descr": "1A23", "time": "1030",
        }}]
        listener._handle_td(messages)
        mock_tracker.handle_td_step.assert_not_called()


# ── _handle_trust ────────────────────────────────────────────────────

class TestHandleTrust:

    def test_movement_msg_dispatched(self, listener, mock_tracker):
        messages = [{"header": {}, "body": {
            "msg_type": "0003",
            "train_id": "512E281Y27",
            "loc_stanox": "87998",
            "planned_event_type": "DEPARTURE",
            "actual_timestamp": "1718444400000",
        }}]
        listener._handle_trust(messages)
        mock_tracker.handle_trust_movement.assert_called_once()
        kwargs = mock_tracker.handle_trust_movement.call_args[1]
        assert kwargs["train_id"] == "512E281Y27"
        assert kwargs["stanox"] == "87998"
        assert kwargs["headcode"] == "2E28"

    def test_activation_not_dispatched(self, listener, mock_tracker):
        messages = [{"header": {}, "body": {
            "msg_type": "0001",
            "train_id": "512E281Y27",
            "loc_stanox": "87998",
        }}]
        listener._handle_trust(messages)
        mock_tracker.handle_trust_movement.assert_not_called()

    def test_missing_body_ignored(self, listener, mock_tracker):
        messages = [{"header": {"msg_type": "0003"}}]
        listener._handle_trust(messages)
        mock_tracker.handle_trust_movement.assert_not_called()

    def test_headcode_extraction(self, listener, mock_tracker):
        messages = [{"header": {}, "body": {
            "msg_type": "0003",
            "train_id": "XX9Z45ABCD",
            "loc_stanox": "12345",
            "planned_event_type": "ARRIVAL",
            "actual_timestamp": "1718444400000",
        }}]
        listener._handle_trust(messages)
        kwargs = mock_tracker.handle_trust_movement.call_args[1]
        assert kwargs["headcode"] == "9Z45"


# ── _handle_td: S-Class signalling messages ──────────────────────────

class TestHandleSfMessages:

    @pytest.fixture
    def mock_history(self):
        return MagicMock()

    @pytest.fixture
    def listener_with_history(self, mock_tracker, mock_history):
        return NRODListener(mock_tracker, history=mock_history)

    def test_sf_msg_correct_area_recorded(self, listener_with_history, mock_history):
        messages = [{"SF_MSG": {
            "area_id": "LA", "address": "16", "data": "43",
        }}]
        listener_with_history._handle_td(messages)
        mock_history.record_sf_event.assert_called_once_with("LA", "16", "43")

    def test_sf_msg_wrong_area_ignored(self, listener_with_history, mock_history):
        messages = [{"SF_MSG": {
            "area_id": "WX", "address": "16", "data": "43",
        }}]
        listener_with_history._handle_td(messages)
        mock_history.record_sf_event.assert_not_called()

    def test_sf_msg_no_history_ignored(self, listener):
        messages = [{"SF_MSG": {
            "area_id": "LA", "address": "16", "data": "43",
        }}]
        # Should not raise even though listener.history is None
        listener._handle_td(messages)

    def test_sg_msg_recorded(self, listener_with_history, mock_history):
        messages = [{"SG_MSG": {
            "area_id": "LA", "address": "2F", "data": "FF",
        }}]
        listener_with_history._handle_td(messages)
        mock_history.record_sf_event.assert_called_once_with("LA", "2F", "FF")

    def test_sh_msg_no_error(self, listener_with_history):
        messages = [{"SH_MSG": {
            "area_id": "LA",
        }}]
        # Should not raise
        listener_with_history._handle_td(messages)

    def test_ct_msg_updates_heartbeat(self, listener_with_history):
        assert listener_with_history.last_heartbeat is None
        messages = [{"CT_MSG": {
            "area_id": "LA", "report_time": "1030",
        }}]
        listener_with_history._handle_td(messages)
        assert isinstance(listener_with_history.last_heartbeat, datetime)

    def test_sf_msg_missing_data_ignored(self, listener_with_history, mock_history):
        messages = [
            {"SF_MSG": {"area_id": "LA", "address": "", "data": "43"}},
            {"SF_MSG": {"area_id": "LA", "address": "16", "data": ""}},
        ]
        listener_with_history._handle_td(messages)
        mock_history.record_sf_event.assert_not_called()


# ── on_message ───────────────────────────────────────────────────────

class TestOnMessage:

    def test_gzip_td_message(self, listener, mock_tracker):
        import gzip as gz
        td_payload = json.dumps([{"CA_MSG": {
            "area_id": "LA", "from": "A001", "to": "A002",
            "descr": "1A23", "time": "1030",
        }}])
        compressed = gz.compress(td_payload.encode("utf-8"))

        frame = MagicMock()
        frame.body = compressed
        frame.headers = {"destination": "/topic/TD_ALL_SIG_AREA"}

        listener.on_message(frame)
        mock_tracker.handle_td_step.assert_called_once()

    def test_plain_json_td_message(self, listener, mock_tracker):
        td_payload = json.dumps([{"CA_MSG": {
            "area_id": "LA", "from": "A001", "to": "A002",
            "descr": "1A23", "time": "1030",
        }}])

        frame = MagicMock()
        frame.body = td_payload
        frame.headers = {"destination": "/topic/TD_ALL_SIG_AREA"}

        listener.on_message(frame)
        mock_tracker.handle_td_step.assert_called_once()

    def test_plain_bytes_non_gzip(self, listener, mock_tracker):
        """Non-gzip bytes fall back to utf-8 decode."""
        td_payload = json.dumps([{"CA_MSG": {
            "area_id": "LA", "from": "A001", "to": "A002",
            "descr": "1A23", "time": "1030",
        }}])

        frame = MagicMock()
        frame.body = td_payload.encode("utf-8")  # bytes but not gzipped
        frame.headers = {"destination": "/topic/TD_ALL_SIG_AREA"}

        listener.on_message(frame)
        mock_tracker.handle_td_step.assert_called_once()

    def test_bad_gzip_data_logged_no_crash(self, listener, mock_tracker):
        frame = MagicMock()
        frame.body = b"\x1f\x8b\x00invalid"  # looks like gzip header but corrupt
        frame.headers = {"destination": "/topic/TD_ALL_SIG_AREA"}

        # Should not raise — error is caught and logged
        listener.on_message(frame)
        mock_tracker.handle_td_step.assert_not_called()

    def test_bad_json_logged_no_crash(self, listener, mock_tracker):
        frame = MagicMock()
        frame.body = "not valid json {{"
        frame.headers = {"destination": "/topic/TD_ALL_SIG_AREA"}

        listener.on_message(frame)
        mock_tracker.handle_td_step.assert_not_called()

    def test_td_destination_routes_to_handle_td(self, listener, mock_tracker):
        frame = MagicMock()
        frame.body = json.dumps([{"CA_MSG": {
            "area_id": "LA", "from": "A001", "to": "A002",
            "descr": "1A23", "time": "1030",
        }}])
        frame.headers = {"destination": "/topic/TD_ALL_SIG_AREA"}

        listener.on_message(frame)
        mock_tracker.handle_td_step.assert_called_once()
        mock_tracker.handle_trust_movement.assert_not_called()

    def test_trust_destination_routes_to_handle_trust(self, listener, mock_tracker):
        frame = MagicMock()
        frame.body = json.dumps([{"header": {}, "body": {
            "msg_type": "0003", "train_id": "512E281Y27",
            "loc_stanox": "87998", "planned_event_type": "DEPARTURE",
            "actual_timestamp": "1718444400000",
        }}])
        frame.headers = {"destination": "/topic/TRAIN_MVT_ALL_TOC"}

        listener.on_message(frame)
        mock_tracker.handle_trust_movement.assert_called_once()
        mock_tracker.handle_td_step.assert_not_called()

    def test_on_message_callback_called(self, mock_tracker):
        callback = MagicMock()
        listener = NRODListener(mock_tracker, on_message_callback=callback)

        frame = MagicMock()
        frame.body = json.dumps([])
        frame.headers = {"destination": "/topic/TD_ALL_SIG_AREA"}

        listener.on_message(frame)
        callback.assert_called_once()

    def test_last_message_time_updated(self, listener):
        frame = MagicMock()
        frame.body = json.dumps([])
        frame.headers = {"destination": "/topic/OTHER"}

        assert listener.last_message_time is None
        listener.on_message(frame)
        assert listener.last_message_time is not None


# ── on_connected / on_disconnected ───────────────────────────────────

class TestConnectionCallbacks:

    def test_on_connected_sets_state(self, listener):
        frame = MagicMock()
        assert listener.connected is False

        listener.on_connected(frame)

        assert listener.connected is True
        assert listener.last_message_time is not None

    def test_on_connected_calls_message_callback(self, mock_tracker):
        callback = MagicMock()
        listener = NRODListener(mock_tracker, on_message_callback=callback)

        listener.on_connected(MagicMock())
        callback.assert_called_once()

    def test_on_disconnected_clears_state(self, listener):
        listener.connected = True

        listener.on_disconnected()

        assert listener.connected is False

    def test_on_disconnected_calls_disconnect_callback(self, mock_tracker):
        disconnect_cb = MagicMock()
        listener = NRODListener(mock_tracker, on_disconnect_callback=disconnect_cb)
        listener.connected = True

        listener.on_disconnected()

        disconnect_cb.assert_called_once()
        assert listener.connected is False

    def test_on_disconnected_no_callback_no_crash(self, listener):
        listener.connected = True
        listener.on_disconnected()  # should not raise

    def test_on_error_no_crash(self, listener):
        frame = MagicMock()
        frame.body = "some error"
        listener.on_error(frame)  # should not raise


# ── Route monitor integration ────────────────────────────────────────

class TestRouteMonitorIntegration:
    """Tests that SF messages are dispatched to the route monitor."""

    @pytest.fixture
    def mock_route_monitor(self):
        return MagicMock()

    @pytest.fixture
    def listener_with_routes(self, mock_tracker, mock_route_monitor):
        return NRODListener(mock_tracker, route_monitor=mock_route_monitor)

    def test_sf_msg_dispatched_to_route_monitor(self, listener_with_routes, mock_route_monitor):
        messages = [{"SF_MSG": {
            "area_id": "LA", "address": "04", "data": "40",
        }}]
        listener_with_routes._handle_td(messages)
        mock_route_monitor.handle_sf_update.assert_called_once_with("LA", "04", "40")

    def test_sg_msg_dispatched_to_route_monitor(self, listener_with_routes, mock_route_monitor):
        messages = [{"SG_MSG": {
            "area_id": "LA", "address": "04", "data": "40",
        }}]
        listener_with_routes._handle_td(messages)
        mock_route_monitor.handle_sf_update.assert_called_once_with("LA", "04", "40")

    def test_sh_msg_dispatched_to_route_monitor(self, listener_with_routes, mock_route_monitor):
        messages = [{"SH_MSG": {"area_id": "LA"}}]
        listener_with_routes._handle_td(messages)
        mock_route_monitor.handle_refresh_complete.assert_called_once_with("LA")

    def test_disconnect_clears_route_monitor(self, listener_with_routes, mock_route_monitor):
        listener_with_routes.connected = True
        listener_with_routes.on_disconnected()
        mock_route_monitor.handle_disconnect.assert_called_once()

    def test_sf_msg_missing_data_not_dispatched(self, listener_with_routes, mock_route_monitor):
        messages = [{"SF_MSG": {"area_id": "LA", "address": "", "data": "40"}}]
        listener_with_routes._handle_td(messages)
        mock_route_monitor.handle_sf_update.assert_not_called()

    def test_no_route_monitor_no_crash(self, listener):
        """Listener without route_monitor should handle SF without error."""
        messages = [{"SF_MSG": {
            "area_id": "LA", "address": "04", "data": "40",
        }}]
        listener._handle_td(messages)  # should not raise
