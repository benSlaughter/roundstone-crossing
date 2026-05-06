"""
Realtime Trains (RTT) API client — polls station data for platform-level train status.
Supplements TD/TRUST with definitive AT_PLATFORM confirmation.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger("crossing.rtt")


class RTTClient:
    """Polls Realtime Trains API for station platform status."""

    BASE_URL = "https://data.rtt.io"

    def __init__(self, stations: list[str], poll_interval: int = 15):
        """
        Args:
            stations: CRS codes to poll (e.g. ["ANG", "GBS"])
            poll_interval: seconds between poll cycles
        """
        self.stations = stations
        self.poll_interval = poll_interval

        self._refresh_token = os.environ.get("RTT_TOKEN", "")
        self._access_token: str | None = None
        self._token_expires: datetime | None = None

        # Rate limit backoff — shared across all requests
        self._retry_after: datetime | None = None
        self._consecutive_429s: int = 0
        self._last_success: datetime | None = None

        # Response cache: {crs: (data_dict, fetched_at)}
        self._cache: dict[str, tuple[dict, datetime]] = {}
        self._cache_ttl = 60  # seconds

        self._thread: threading.Thread | None = None
        self._running = False
        self._on_update: Callable | None = None
        self._has_active_trains: Callable = lambda: False  # gate for polling

        if not self._refresh_token:
            logger.warning("RTT_TOKEN not set — RTT integration disabled")

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
        logger.info(f"RTT polling started for {', '.join(self.stations)} (every {self.poll_interval}s)")

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

    def _fetch_station(self, crs: str, *, bypass_cache: bool = False) -> dict | None:
        """Fetch station data from RTT, with 60s response caching.

        Returns parsed JSON dict, or None on error/rate-limit/empty.
        """
        now = datetime.now(timezone.utc)

        # Check cache first
        if not bypass_cache and crs in self._cache:
            data, fetched_at = self._cache[crs]
            age = (now - fetched_at).total_seconds()
            if age < self._cache_ttl:
                logger.debug(f"RTT cache hit for {crs} ({age:.0f}s old)")
                return data

        # Need fresh data — ensure token and check rate limit
        if not self._ensure_token():
            return None

        if self._retry_after and now < self._retry_after:
            return None

        try:
            resp = requests.get(
                f"{self.BASE_URL}/gb-nr/location",
                params={"code": crs},
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=10,
            )

            if resp.status_code == 429:
                self._handle_rate_limit(resp)
                return None

            if resp.status_code == 204:
                return None

            resp.raise_for_status()
            data = resp.json()
            self._cache[crs] = (data, now)
            self._consecutive_429s = 0
            self._last_success = now
            return data

        except Exception as e:
            logger.error(f"RTT fetch {crs} failed: {e}")
            return None

    def _poll_station(self, crs: str):
        """Poll a single station for current services."""
        data = self._fetch_station(crs)
        if data is None:
            return

        station_name = data.get("query", {}).get("location", {}).get("description", crs)
        services = data.get("services", [])

        for svc in services:
            self._process_service(svc, station_name)

    def get_upcoming(self, crs: str, limit: int = 5) -> list[dict]:
        """Query upcoming services at a station. Returns list of service dicts."""
        data = self._fetch_station(crs)
        if data is None:
            return []
        try:
            station_name = data.get("query", {}).get("location", {}).get("description", crs)
            services = data.get("services", [])

            results = []
            for svc in services:
                temporal = svc.get("temporalData", {})
                schedule = svc.get("scheduleMetadata", {})
                status = temporal.get("status", "")

                # Skip trains that have already departed
                if status in ("HAS_DEPTED",):
                    continue

                headcode = schedule.get("trainReportingIdentity", "?")
                display_as = temporal.get("displayAs", "CALL")
                operator = schedule.get("operator", {}).get("name", "")

                # Arrival time from nested structure
                arr_data = temporal.get("arrival", {})
                rt_arr = arr_data.get("realtimeForecast", "")
                sched_arr = arr_data.get("scheduleAdvertised", "")
                arr_iso = rt_arr or sched_arr
                arr_display = arr_iso[11:16] if len(arr_iso) >= 16 else ""
                arr_delayed = rt_arr and sched_arr and rt_arr != sched_arr
                sched_arr_display = sched_arr[11:16] if arr_delayed and len(sched_arr) >= 16 else ""

                # Departure time from nested structure
                dep_data = temporal.get("departure", {})
                rt_dep = dep_data.get("realtimeForecast", "")
                sched_dep = dep_data.get("scheduleAdvertised", "")
                dep_iso = rt_dep or sched_dep
                dep_display = dep_iso[11:16] if len(dep_iso) >= 16 else ""
                is_delayed = rt_dep and sched_dep and rt_dep != sched_dep
                sched_display = sched_dep[11:16] if is_delayed and len(sched_dep) >= 16 else ""

                # Platform
                platform = temporal.get("platform", "?")

                # Origin/destination from nested location structure
                origin_list = svc.get("origin", [])
                dest_list = svc.get("destination", [])
                origin = origin_list[0].get("location", {}).get("description", "") if origin_list else ""
                dest = dest_list[0].get("location", {}).get("description", "") if dest_list else ""

                # Direction: platform 1 = east (up), platform 2 = west (down)
                # Fallback: infer from destination
                if platform == "1":
                    direction = "east"
                elif platform == "2":
                    direction = "west"
                else:
                    east_destinations = ("Brighton", "Worthing", "Hove", "Hastings", "Eastbourne",
                                         "Lewes", "Horsham", "London", "Victoria", "Gatwick",
                                         "Croydon", "Clapham")
                    west_destinations = ("Littlehampton", "Portsmouth", "Southampton", "Bognor", "Chichester", "Havant")
                    if any(d in dest for d in east_destinations):
                        direction = "east"
                    elif any(d in dest for d in west_destinations):
                        direction = "west"
                    else:
                        direction = ""

                results.append({
                    "headcode": headcode,
                    "station": station_name,
                    "platform": platform,
                    "direction": direction,
                    "arrival": arr_display,
                    "arrival_scheduled": sched_arr_display,
                    "arrival_iso": arr_iso or None,
                    "departure": dep_display,
                    "departure_scheduled": sched_display,
                    "departure_iso": dep_iso or None,
                    "status": status,
                    "display_as": display_as,
                    "operator": operator,
                    "origin": origin,
                    "destination": dest,
                })
                if len(results) >= limit:
                    break
            return results
        except Exception as e:
            logger.error(f"RTT upcoming query failed: {e}")
            return []

    def get_raw_services(self, crs: str) -> dict:
        """Debug: return raw RTT response for a station."""
        data = self._fetch_station(crs)
        if data is None:
            return {"error": "fetch failed"}
        return data

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

    @property
    def rate_limit_info(self) -> dict:
        """Expose current rate-limit state for health reporting."""
        now = datetime.now(timezone.utc)

        # Actively backing off
        if self._retry_after and now < self._retry_after:
            remaining = (self._retry_after - now).total_seconds()
            return {
                "active": True,
                "until": self._retry_after.isoformat(),
                "remaining_secs": round(remaining),
                "consecutive_429s": self._consecutive_429s,
            }

        # Between backoff windows but still repeatedly failing
        if self._consecutive_429s >= 2:
            return {
                "active": True,
                "until": None,
                "remaining_secs": 0,
                "consecutive_429s": self._consecutive_429s,
            }

        return {"active": False}

    def _handle_rate_limit(self, resp: requests.Response):
        """Handle 429 rate limit response. Cap backoff at 5 minutes to recover sooner."""
        raw_retry = int(resp.headers.get("Retry-After", 60))
        capped = min(raw_retry, 300)  # cap at 5 minutes — re-check rather than blindly wait hours
        self._retry_after = datetime.now(timezone.utc) + timedelta(seconds=capped)
        self._consecutive_429s += 1
        logger.warning(f"RTT rate limited — server asked for {raw_retry}s, backing off {capped}s (streak: {self._consecutive_429s})")
