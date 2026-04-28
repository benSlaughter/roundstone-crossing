"""
NROD STOMP feed listener — connects to Network Rail open data feeds
and dispatches TD and TRUST messages to the train tracker.
"""

import gzip
import json
import logging
import os
import threading
from datetime import datetime, timezone
from time import sleep

import stomp

from .tracker import TrainTracker

logger = logging.getLogger("crossing.feed")


class NRODListener(stomp.ConnectionListener):
    """Handles incoming STOMP messages from Network Rail."""

    def __init__(self, tracker: TrainTracker, on_message_callback=None):
        self.tracker = tracker
        self.on_message_callback = on_message_callback
        self.connected = False
        self.last_message_time: datetime | None = None

    def on_message(self, frame):
        try:
            body = frame.body
            # NROD messages may be gzip-compressed
            if isinstance(body, bytes):
                try:
                    body = gzip.decompress(body).decode("utf-8")
                except (gzip.BadGzipFile, OSError):
                    body = body.decode("utf-8")

            messages = json.loads(body)
            self.last_message_time = datetime.now(timezone.utc)
            dest = frame.headers.get("destination", "")

            if "TD_" in dest:
                self._handle_td(messages)
            elif "TRAIN_MVT_" in dest:
                self._handle_trust(messages)

            if self.on_message_callback:
                self.on_message_callback(self.last_message_time)

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)

    def on_connected(self, frame):
        self.connected = True
        logger.info("✅ Connected to NROD")

    def on_disconnected(self):
        self.connected = False
        logger.warning("❌ Disconnected from NROD")

    def on_error(self, frame):
        logger.error(f"STOMP error: {frame.body}")

    def _handle_td(self, messages: list):
        """Process Train Describer messages."""
        for msg in messages:
            # TD messages come in several types: CA (berth step), CB (berth cancel),
            # CC (berth interpose), CT (heartbeat)
            for msg_type in ("CA_MSG", "CB_MSG", "CC_MSG"):
                if msg_type in msg:
                    data = msg[msg_type]
                    area = data.get("area_id", "")

                    # Filter to our area of interest
                    if area != self.tracker.area_id:
                        continue

                    if msg_type == "CA_MSG":
                        # Berth step: train moved from one berth to another
                        from_berth = data.get("from", "")
                        to_berth = data.get("to", "")
                        headcode = data.get("descr", "")
                        timestamp = self._parse_td_time(data.get("time", ""))

                        if headcode and to_berth:
                            self.tracker.handle_td_step(from_berth, to_berth, headcode, timestamp)

                    elif msg_type == "CC_MSG":
                        # Berth interpose: train appeared in a berth (no "from")
                        to_berth = data.get("to", "")
                        headcode = data.get("descr", "")
                        timestamp = self._parse_td_time(data.get("time", ""))

                        if headcode and to_berth:
                            self.tracker.handle_td_step("", to_berth, headcode, timestamp)

                    elif msg_type == "CB_MSG":
                        # Berth cancel: train description removed from berth
                        from_berth = data.get("from", "")
                        headcode = data.get("descr", "")
                        timestamp = self._parse_td_time(data.get("time", ""))

                        if headcode and from_berth:
                            self.tracker.handle_td_cancel(from_berth, headcode, timestamp)

    def _handle_trust(self, messages: list):
        """Process TRUST train movement messages."""
        for msg in messages:
            if "body" not in msg:
                continue
            body = msg["body"]
            msg_type = body.get("msg_type", "")

            # msg_type 3 = movement (arrival/departure/pass)
            if msg_type != "0003":
                continue

            train_id = body.get("train_id", "")
            stanox = body.get("loc_stanox", "")
            event_type = body.get("planned_event_type", "")

            # Get the actual timestamp
            actual_timestamp = body.get("actual_timestamp", "")
            if actual_timestamp:
                try:
                    ts = datetime.fromtimestamp(int(actual_timestamp) / 1000, tz=timezone.utc)
                except (ValueError, TypeError):
                    ts = datetime.now(timezone.utc)
            else:
                ts = datetime.now(timezone.utc)

            # Headcode is chars 3-6 of the 10-char train_id (e.g., "512E281Y27" → "2E28")
            headcode = train_id[2:6] if len(train_id) >= 6 else ""

            if train_id and stanox:
                self.tracker.handle_trust_movement(
                    train_id=train_id,
                    stanox=stanox,
                    event_type=event_type,
                    actual_time=ts,
                    headcode=headcode,
                )

    @staticmethod
    def _parse_td_time(time_str: str) -> datetime:
        """Parse TD timestamp (HHMM format, today's date assumed)."""
        now = datetime.now(timezone.utc)
        try:
            hour = int(time_str[:2])
            minute = int(time_str[2:4])
            return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except (ValueError, IndexError):
            return now


class NRODFeed:
    """Manages the STOMP connection to Network Rail Open Data."""

    def __init__(self, tracker: TrainTracker, on_message_callback=None):
        self.tracker = tracker
        self.listener = NRODListener(tracker, on_message_callback)
        self.connection: stomp.Connection | None = None
        self._running = False

    def start(self):
        """Connect to NROD and subscribe to feeds."""
        username = os.environ.get("NROD_USERNAME", "")
        password = os.environ.get("NROD_PASSWORD", "")

        if not username or not password:
            logger.error("❌ NROD_USERNAME and NROD_PASSWORD must be set")
            return False

        self.connection = stomp.Connection12(
            [("publicdatafeeds.networkrail.co.uk", 61618)],
            keepalive=True,
            heartbeats=(15000, 15000),
        )
        self.connection.set_listener("nrod", self.listener)

        try:
            self.connection.connect(
                username=username,
                passcode=password,
                wait=True,
            )
        except Exception as e:
            logger.error(f"❌ Failed to connect to NROD: {e}")
            return False

        # Subscribe to TD feed (train describer — berth stepping)
        self.connection.subscribe(
            destination="/topic/TD_ALL_SIG_AREA",
            id="td-sub",
            ack="auto",
        )
        logger.info("📡 Subscribed to TD feed")

        # Subscribe to TRUST feed (train movements)
        self.connection.subscribe(
            destination="/topic/TRAIN_MVT_ALL_TOC",
            id="trust-sub",
            ack="auto",
        )
        logger.info("📡 Subscribed to TRUST feed")

        self._running = True
        return True

    def stop(self):
        """Disconnect from NROD."""
        self._running = False
        if self.connection and self.connection.is_connected():
            self.connection.disconnect()
            logger.info("📡 Disconnected from NROD")

    @property
    def is_connected(self) -> bool:
        return self.connection is not None and self.connection.is_connected()

    @property
    def last_message_time(self) -> datetime | None:
        return self.listener.last_message_time
