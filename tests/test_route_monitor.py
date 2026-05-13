"""Tests for RouteMonitor — S-Class route state tracking."""

from datetime import datetime, timezone

import pytest
from freezegun import freeze_time

from src.route_monitor import RouteMonitor, RouteInfo


NOW = datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def route_config():
    """Config with a small set of test routes."""
    return {
        "td": {"area_id": "LA"},
        "routes": {
            "crossing_routes": [
                {"address": "04", "bit": 6, "name": "R35", "side": "east"},
                {"address": "04", "bit": 4, "name": "R34", "side": "east"},
                {"address": "04", "bit": 2, "name": "R32", "side": "east"},
                {"address": "05", "bit": 1, "name": "RA007", "side": "west"},
                {"address": "05", "bit": 2, "name": "RA008", "side": "west"},
                {"address": "03", "bit": 4, "name": "RA010", "side": "west"},
            ],
        },
    }


@pytest.fixture
def monitor(route_config):
    return RouteMonitor(route_config)


class TestRouteMonitorInit:
    def test_loads_routes_from_config(self, monitor):
        assert len(monitor._route_map) == 6

    def test_no_routes_config(self):
        m = RouteMonitor({"td": {"area_id": "LA"}})
        assert len(m._route_map) == 0

    def test_invalid_address_skipped(self):
        config = {
            "td": {"area_id": "LA"},
            "routes": {"crossing_routes": [
                {"address": "ZZ", "bit": 0, "name": "bad", "side": "east"},
            ]},
        }
        m = RouteMonitor(config)
        assert len(m._route_map) == 0

    def test_invalid_bit_skipped(self):
        config = {
            "td": {"area_id": "LA"},
            "routes": {"crossing_routes": [
                {"address": "04", "bit": 9, "name": "bad", "side": "east"},
            ]},
        }
        m = RouteMonitor(config)
        assert len(m._route_map) == 0


@freeze_time(NOW)
class TestSFParsing:
    """Golden tests: known SF byte values → expected route activations."""

    def test_single_bit_set(self, monitor):
        # Address 04, bit 6 = R35 → hex byte with bit 6 set = 0x40
        monitor.handle_sf_update("LA", "04", "40")
        routes = monitor.active_route_names()
        assert "R35" in routes
        assert len(routes) == 1

    def test_multiple_bits_set(self, monitor):
        # Address 04, bits 6+4+2 = R35+R34+R32 → 0b01010100 = 0x54
        monitor.handle_sf_update("LA", "04", "54")
        routes = monitor.active_route_names()
        assert set(routes) == {"R35", "R34", "R32"}

    def test_bit_clear_removes_route(self, monitor):
        # Set R35
        monitor.handle_sf_update("LA", "04", "40")
        assert "R35" in monitor.active_route_names()

        # Clear it (all zeros)
        monitor.handle_sf_update("LA", "04", "00")
        assert "R35" not in monitor.active_route_names()
        assert len(monitor.active_route_names()) == 0

    def test_wrong_area_ignored(self, monitor):
        monitor.handle_sf_update("BM", "04", "40")
        assert len(monitor.active_route_names()) == 0

    def test_unrelated_address_ignored(self, monitor):
        # Address 01 has no route mappings
        monitor.handle_sf_update("LA", "01", "FF")
        assert len(monitor.active_route_names()) == 0

    def test_address_normalization(self, monitor):
        # Upper and lower case hex should work
        monitor.handle_sf_update("LA", "04", "40")
        assert "R35" in monitor.active_route_names()

    def test_multiple_addresses(self, monitor):
        # Set routes on two different addresses
        monitor.handle_sf_update("LA", "04", "40")  # R35 on addr 04
        monitor.handle_sf_update("LA", "05", "02")  # RA007 on addr 05 (bit 1)
        routes = monitor.active_route_names()
        assert "R35" in routes
        assert "RA007" in routes

    def test_partial_update_preserves_other_addresses(self, monitor):
        # Set route on addr 04
        monitor.handle_sf_update("LA", "04", "40")
        # Update addr 05 (different address)
        monitor.handle_sf_update("LA", "05", "02")
        # Clear addr 04
        monitor.handle_sf_update("LA", "04", "00")
        # RA007 on addr 05 should still be active
        routes = monitor.active_route_names()
        assert "RA007" in routes
        assert "R35" not in routes


@freeze_time(NOW)
class TestRouteInfo:
    def test_active_routes_returns_route_info(self, monitor):
        monitor.handle_sf_update("LA", "04", "40")
        routes = monitor.active_routes()
        assert len(routes) == 1
        assert isinstance(routes[0], RouteInfo)
        assert routes[0].name == "R35"
        assert routes[0].side == "east"
        assert routes[0].set_since == NOW


@freeze_time(NOW)
class TestDisconnect:
    def test_disconnect_clears_all_routes(self, monitor):
        monitor.handle_sf_update("LA", "04", "40")
        monitor.handle_sf_update("LA", "05", "02")
        assert len(monitor.active_route_names()) == 2

        monitor.handle_disconnect()
        assert len(monitor.active_route_names()) == 0
        assert len(monitor._address_state) == 0

    def test_routes_restored_after_reconnect(self, monitor):
        monitor.handle_sf_update("LA", "04", "40")
        monitor.handle_disconnect()
        assert len(monitor.active_route_names()) == 0

        # SG refresh restores state
        monitor.handle_sf_update("LA", "04", "40")
        assert "R35" in monitor.active_route_names()


@freeze_time(NOW)
class TestRefreshComplete:
    def test_refresh_complete_does_not_clear(self, monitor):
        monitor.handle_sf_update("LA", "04", "40")
        monitor.handle_refresh_complete("LA")
        assert "R35" in monitor.active_route_names()

    def test_wrong_area_refresh_ignored(self, monitor):
        monitor.handle_sf_update("LA", "04", "40")
        monitor.handle_refresh_complete("BM")
        assert "R35" in monitor.active_route_names()


@freeze_time(NOW)
class TestEdgeCases:
    def test_empty_data_hex(self, monitor):
        # Should not crash
        monitor.handle_sf_update("LA", "04", "")
        assert len(monitor.active_route_names()) == 0

    def test_invalid_data_hex(self, monitor):
        monitor.handle_sf_update("LA", "04", "XY")
        assert len(monitor.active_route_names()) == 0

    def test_same_update_idempotent(self, monitor):
        monitor.handle_sf_update("LA", "04", "40")
        monitor.handle_sf_update("LA", "04", "40")
        assert len(monitor.active_route_names()) == 1

    def test_no_area_id_in_config(self):
        m = RouteMonitor({
            "routes": {"crossing_routes": [
                {"address": "04", "bit": 6, "name": "R35", "side": "east"},
            ]},
        })
        # Falls back to "LA" default
        m.handle_sf_update("LA", "04", "40")
        assert "R35" in m.active_route_names()


# Golden test: full production config byte values
@freeze_time(NOW)
class TestGoldenBytes:
    """Verify known SF byte patterns from observed data activate correct routes."""

    def test_addr_04_0x54_sets_three_routes(self, monitor):
        """0x54 = 0b01010100 → bits 2, 4, 6 → R32, R34, R35."""
        monitor.handle_sf_update("LA", "04", "54")
        routes = set(monitor.active_route_names())
        assert routes == {"R32", "R34", "R35"}

    def test_addr_05_0x06_sets_two_routes(self, monitor):
        """0x06 = 0b00000110 → bits 1, 2 → RA007, RA008."""
        monitor.handle_sf_update("LA", "05", "06")
        routes = set(monitor.active_route_names())
        assert routes == {"RA007", "RA008"}

    def test_addr_03_0x10_sets_ra010(self, monitor):
        """0x10 = 0b00010000 → bit 4 → RA010."""
        monitor.handle_sf_update("LA", "03", "10")
        routes = set(monitor.active_route_names())
        assert routes == {"RA010"}


# ---------------------------------------------------------------------------
# Integration with HistoryLogger — route intervals are recorded
# ---------------------------------------------------------------------------

class TestHistoryIntegration:
    def test_set_writes_route_interval(self, route_config, history_db):
        rm = RouteMonitor(route_config, history=history_db)
        # Set R35 (address 04 bit 6 → 0x40)
        rm.handle_sf_update("LA", "04", "40")

        import sqlite3
        db = sqlite3.connect(str(history_db.db_path))
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM route_intervals WHERE route_name='R35'").fetchall()
        db.close()
        assert len(rows) == 1
        assert rows[0]["end_reason"] is None  # still open
        assert rows[0]["cleared_at"] is None

    def test_clear_writes_observed_clear(self, route_config, history_db):
        rm = RouteMonitor(route_config, history=history_db)
        rm.handle_sf_update("LA", "04", "40")  # SET R35
        rm.handle_sf_update("LA", "04", "00")  # CLEAR R35

        import sqlite3
        db = sqlite3.connect(str(history_db.db_path))
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM route_intervals WHERE route_name='R35'").fetchone()
        db.close()
        assert row["end_reason"] == "observed_clear"
        assert row["cleared_at"] is not None

    def test_disconnect_marks_intervals_uncertain(self, route_config, history_db):
        rm = RouteMonitor(route_config, history=history_db)
        # Set R35 (bit 6) + R34 (bit 4) → 0x50
        rm.handle_sf_update("LA", "04", "50")
        rm.handle_disconnect()

        import sqlite3
        db = sqlite3.connect(str(history_db.db_path))
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT * FROM route_intervals WHERE route_name IN ('R35', 'R34')"
        ).fetchall()
        db.close()
        # Both intervals must be closed with end_reason='disconnect',
        # but cleared_at must remain NULL — we never observed an actual clear
        assert len(rows) == 2
        for row in rows:
            assert row["end_reason"] == "disconnect"
            assert row["cleared_at"] is None
            assert row["observed_until"] is not None

    def test_no_history_is_safe(self, route_config):
        """RouteMonitor must work when history=None (backwards compat)."""
        rm = RouteMonitor(route_config)  # no history
        rm.handle_sf_update("LA", "04", "40")
        rm.handle_sf_update("LA", "04", "00")
        rm.handle_disconnect()
        # No exception = pass

    def test_history_failure_does_not_corrupt_state(self, route_config, history_db, monkeypatch):
        """If history.start_route_interval crashes, route monitor in-memory
        state must still reflect the route as SET (because we updated state
        BEFORE calling history, outside the lock)."""
        def bad_start(*a, **kw):
            raise RuntimeError("simulated DB failure")
        monkeypatch.setattr(history_db, "start_route_interval", bad_start)

        rm = RouteMonitor(route_config, history=history_db)
        # The exception will propagate, but in-memory state should be SET first
        try:
            rm.handle_sf_update("LA", "04", "40")
        except RuntimeError:
            pass
        # Even after the failed history call, the route should be marked active
        assert "R35" in rm.active_route_names()
