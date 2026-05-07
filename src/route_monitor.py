"""
Route monitor — tracks LA signalling route state from S-Class (SF/SG) messages.

Routes near the crossing indicate barriers are down (MCB-CCTV procedure:
signaller must lower barriers before setting a route). This module parses
SF signalling data, maintains per-bit route state, and exposes which
crossing routes are currently SET.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("crossing.route_monitor")


@dataclass
class RouteInfo:
    """A crossing route that is currently SET."""
    name: str
    side: str          # "east" or "west" relative to crossing
    set_since: datetime | None = None


class RouteMonitor:
    """Monitors LA route state from S-Class signalling messages.

    Maintains the current state of each SF address byte and extracts
    individual route bits for addresses/bits that map to crossing routes.
    Handles SF (delta) and SG (refresh) messages, and resets state on
    disconnect/stale conditions.
    """

    def __init__(self, config: dict):
        self._lock = threading.Lock()
        self._area_id = config.get("td", {}).get("area_id", "LA")

        # Parse crossing routes from config into normalised lookup
        # Key: (address_int, bit_index), Value: (route_name, side)
        self._route_map: dict[tuple[int, int], tuple[str, str]] = {}
        self._load_routes(config)

        # Addresses we care about (for quick filtering)
        self._watched_addresses: set[int] = {addr for addr, _ in self._route_map}

        # Current state: full byte per address (int 0-255)
        self._address_state: dict[int, int] = {}

        # Track which routes are currently SET
        self._active_routes: dict[str, RouteInfo] = {}

        if self._route_map:
            logger.info(f"Route monitor: {len(self._route_map)} crossing routes configured "
                        f"across {len(self._watched_addresses)} addresses")
        else:
            logger.warning("Route monitor: no crossing routes configured")

    def _load_routes(self, config: dict):
        """Load crossing route bit map from config."""
        routes_config = config.get("routes", {}).get("crossing_routes", [])
        seen: set[tuple[int, int]] = set()

        for route in routes_config:
            addr_raw = route.get("address", "")
            bit = route.get("bit")
            name = route.get("name", "")
            side = route.get("side", "")

            if not addr_raw or bit is None or not name:
                logger.warning(f"Skipping incomplete route config: {route}")
                continue

            # Normalise address to int
            try:
                addr_int = int(str(addr_raw), 16)
            except ValueError:
                logger.warning(f"Invalid address '{addr_raw}' for route {name}")
                continue

            if not (0 <= bit <= 7):
                logger.warning(f"Bit {bit} out of range (0-7) for route {name}")
                continue

            key = (addr_int, bit)
            if key in seen:
                logger.warning(f"Duplicate route mapping for address 0x{addr_int:02X} bit {bit}")
                continue

            seen.add(key)
            self._route_map[key] = (name, side)

    def handle_sf_update(self, area_id: str, address: str, data_hex: str):
        """Process an SF or SG signalling message.

        Args:
            area_id: TD area (e.g., "LA")
            address: Hex address string (e.g., "04")
            data_hex: Hex data string (e.g., "3F") representing 8 bits
        """
        if area_id != self._area_id:
            return

        try:
            addr_int = int(address, 16)
        except (ValueError, TypeError):
            return

        if addr_int not in self._watched_addresses:
            return

        try:
            data_int = int(data_hex, 16)
        except (ValueError, TypeError):
            return

        with self._lock:
            old_state = self._address_state.get(addr_int, 0)
            self._address_state[addr_int] = data_int

            if data_int == old_state:
                return

            now = datetime.now(timezone.utc)
            self._update_routes_for_address(addr_int, data_int, now)

    def handle_refresh_complete(self, area_id: str):
        """Handle SH_MSG (refresh complete). After SG refresh, any address
        not refreshed should be considered cleared."""
        if area_id != self._area_id:
            return
        # SG messages for each address arrive before SH. The SH just marks
        # the refresh as complete. No action needed — individual SG messages
        # already updated the state via handle_sf_update.
        logger.debug("Route monitor: SG refresh complete")

    def handle_disconnect(self):
        """Reset all route state on feed disconnect.
        Routes will be re-established via SG refresh on reconnect."""
        with self._lock:
            if self._active_routes:
                logger.info(f"Route monitor: clearing {len(self._active_routes)} active routes (disconnect)")
            self._address_state.clear()
            self._active_routes.clear()

    def active_routes(self) -> list[RouteInfo]:
        """Return list of crossing routes currently SET."""
        with self._lock:
            return list(self._active_routes.values())

    def active_route_names(self) -> list[str]:
        """Return just the names of crossing routes currently SET."""
        with self._lock:
            return [r.name for r in self._active_routes.values()]

    def _update_routes_for_address(self, addr_int: int, data_int: int, now: datetime):
        """Update active routes based on new byte value for an address.
        Must be called with self._lock held."""
        for bit in range(8):
            key = (addr_int, bit)
            if key not in self._route_map:
                continue

            route_name, side = self._route_map[key]
            bit_set = bool(data_int & (1 << bit))

            if bit_set and route_name not in self._active_routes:
                self._active_routes[route_name] = RouteInfo(
                    name=route_name, side=side, set_since=now,
                )
                logger.info(f"Route SET: {route_name} ({side})")

            elif not bit_set and route_name in self._active_routes:
                info = self._active_routes.pop(route_name)
                held = ""
                if info.set_since:
                    secs = (now - info.set_since).total_seconds()
                    held = f" (held {secs:.0f}s)"
                logger.info(f"Route CLEAR: {route_name} ({side}){held}")
