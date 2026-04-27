"""
Realtime Trains (RTT) API client — polls station data for platform-level train status.
Supplements TD/TRUST with definitive AT_PLATFORM confirmation.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable

import requests

logger = logging.getLogger("crossing.rtt")


class RTTClient:
    """Polls Realtime Trains API for station platform status."""

    BASE_URL = "https://data.rtt.io"

    def __init__(self, stations: list[str], poll_interval: int = 15):
        """
        Args:
            stations: CRS codes to poll (e.g. ["ANG", "GOR"])
            poll_interval: seconds between poll cycles
        """
        self.stations = stations
        self.poll_interval = poll_interval

        self._refresh_token = os.environ.get("RTT_TOKEN", "")
        self._access_token: Optional[str] = None
        self._token_expires: Optional[datetime] = None

        # Rate limit backoff — shared across all requests
        self._retry_after: Optional[datetime] = None

        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._on_update: Optional[Callable] = None
        self._has_active_trains: Callable = lambda: False  # gate for polling

        if not self._refresh_token:
            logger.warning("⚠️  RTT_TOKEN not set — RTT integration disabled")

    def set_callback(self, callback: Callable):
        """Set callback for station updates: callback(headcode, station, platform, status, direction_hint)"""
        self._on_update = callback

    def set_active_check(self, check: Callable):
        """Set a function that returns True when there are trains worth polling for."""
        self._has_active_trains = check

    def start(self):
        """Start polling in a background thread."""
        if not self._refresh_token:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="rtt-poller")
        self._thread.start()
        logger.info(f"📡 RTT polling started for {', '.join(self.stations)} (every {self.poll_interval}s)")

    def stop(self):
        """Stop polling."""
        self._running = False

    def _poll_loop(self):
        """Main polling loop — runs in background thread. Only polls when trains are active."""
        while self._running:
            try:
                if self._has_active_trains():
                    self._poll_all_stations()
            except Exception as e:
                logger.error(f"RTT poll error: {e}")
            time.sleep(self.poll_interval)

    def _poll_all_stations(self):
        """Poll all configured stations."""
        # Check global rate limit backoff
        if self._retry_after and datetime.now(timezone.utc) < self._retry_after:
            wait = (self._retry_after - datetime.now(timezone.utc)).total_seconds()
            logger.debug(f"RTT rate limited, waiting {wait:.0f}s")
            return

        # Ensure we have a valid access token
        if not self._ensure_token():
            return

        for crs in self.stations:
            if not self._running:
                break
            self._poll_station(crs)

    def _ensure_token(self) -> bool:
        """Get or refresh the access token. Returns True if token is valid."""
        now = datetime.now(timezone.utc)

        # Refresh if token is missing or expires within 2 minutes
        if self._access_token and self._token_expires and (self._token_expires - now) > timedelta(minutes=2):
            return True

        try:
            resp = requests.get(
                f"{self.BASE_URL}/api/get_access_token",
                headers={"Authorization": f"Bearer {self._refresh_token}"},
                timeout=10,
            )

            if resp.status_code == 429:
                self._handle_rate_limit(resp)
                return False

            resp.raise_for_status()
            data = resp.json()

            self._access_token = data["token"]
            # Parse validUntil (ISO 8601)
            valid_until = data.get("validUntil", "")
            if valid_until:
                self._token_expires = datetime.fromisoformat(valid_until).astimezone(timezone.utc)
            else:
                self._token_expires = now + timedelta(minutes=15)

            logger.debug(f"RTT token refreshed, valid until {self._token_expires.isoformat()}")
            return True

        except Exception as e:
            logger.error(f"RTT token refresh failed: {e}")
            return False

    def _poll_station(self, crs: str):
        """Poll a single station for current services."""
        try:
            resp = requests.get(
                f"{self.BASE_URL}/gb-nr/location",
                params={"code": crs},
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=10,
            )

            if resp.status_code == 429:
                self._handle_rate_limit(resp)
                return

            if resp.status_code == 204:
                # No services found
                return

            resp.raise_for_status()
            data = resp.json()

            station_name = data.get("query", {}).get("location", {}).get("description", crs)
            services = data.get("services", [])

            for svc in services:
                self._process_service(svc, station_name)

        except requests.exceptions.RequestException as e:
            logger.error(f"RTT poll {crs} failed: {e}")

    def _process_service(self, svc: dict, station_name: str):
        """Extract relevant data from a service and notify callback."""
        if not self._on_update:
            return

        temporal = svc.get("temporalData", {})
        status = temporal.get("status")
        display_as = temporal.get("displayAs", "")

        schedule = svc.get("scheduleMetadata", {})
        headcode = schedule.get("trainReportingIdentity")
        if not headcode:
            return

        # Only care about trains with actionable status
        if status not in ("AT_PLATFORM", "ARRIVING", "DEPARTING", "DEPART_READY", "DEPART_PREPARING"):
            return

        # Not a passenger train? Skip
        if not schedule.get("inPassengerService", True):
            return

        # Extract platform
        location_meta = svc.get("locationMetadata", {})
        platform_data = location_meta.get("platform", {})
        platform = platform_data.get("actual") or platform_data.get("planned")

        # Get origin/destination for direction hinting
        origin_codes = []
        dest_codes = []
        for o in svc.get("origin", []):
            loc = o.get("location", {})
            origin_codes.extend(loc.get("longCodes", []))
        for d in svc.get("destination", []):
            loc = d.get("location", {})
            dest_codes.extend(loc.get("longCodes", []))

        self._on_update(
            headcode=headcode,
            station=station_name,
            platform=platform,
            status=status,
            origin_codes=origin_codes,
            dest_codes=dest_codes,
        )

    def _handle_rate_limit(self, resp: requests.Response):
        """Handle 429 rate limit response."""
        retry_after = int(resp.headers.get("Retry-After", 60))
        self._retry_after = datetime.now(timezone.utc) + timedelta(seconds=retry_after)
        logger.warning(f"⚠️  RTT rate limited — backing off {retry_after}s")
