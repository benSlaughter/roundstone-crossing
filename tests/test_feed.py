"""Tests for NRODListener message parsing and dispatch."""

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
