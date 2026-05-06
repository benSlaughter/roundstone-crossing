"""
API server — exposes crossing status, predictions, health, and history.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .inferrer import CrossingInferrer
from .models import CrossingState
from .history import HistoryLogger
from .tracker import TrainTracker
from .utils import merge_closure_windows

STATIC_DIR = Path(__file__).parent.parent / "static"
_START_TIME = datetime.now(timezone.utc)
_GB_TZ = ZoneInfo("Europe/London")


def _parse_rtt_time(iso_str: str) -> datetime:
    """Parse an RTT ISO timestamp (GB local time) to UTC-aware datetime."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_GB_TZ)
    return dt.astimezone(timezone.utc)


def create_app(tracker: TrainTracker, inferrer: CrossingInferrer, history: HistoryLogger,
               rtt_client=None) -> FastAPI:
    app = FastAPI(title="Roundstone Crossing Predictor")

    @app.middleware("http")
    async def add_csp_header(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; img-src 'self' data:; connect-src 'self'"
        )
        return response

    @app.get("/")
    async def dashboard():
        """Serve the web dashboard."""
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/status")
    async def status():
        """Current inferred crossing state."""
        return inferrer.status.to_dict()

    @app.get("/diagram")
    async def diagram():
        """All tracked trains with berth positions for the schematic diagram."""
        from .models import TrainPhase
        with tracker._lock:
            trains_snapshot = dict(tracker.trains)
        trains = []
        for hc, t in trains_snapshot.items():
            if t.phase in (TrainPhase.LOST,):
                continue
            trains.append({
                "headcode": t.headcode,
                "direction": t.direction.value if t.direction else None,
                "phase": t.phase.value,
                "last_berth": t.last_berth,
                "station": t.station,
                "sub_position": t.sub_position,
                "confidence": round(t.confidence, 2),
                "age_secs": round(t.age_secs),
            })
        return {
            "state": inferrer.status.state.value,
            "confidence": round(inferrer.status.confidence, 2),
            "trains": trains,
        }

    @app.get("/predictions")
    async def predictions():
        """Upcoming trains and predicted closure windows."""
        trains = inferrer.status.active_trains
        return {
            "crossing_state": inferrer.status.state.value,
            "trains": [
                {
                    "headcode": t.headcode,
                    "direction": t.direction.value if t.direction else None,
                    "phase": t.phase.value,
                    "predicted_at_crossing": t.predicted_at_crossing.isoformat() if t.predicted_at_crossing else None,
                    "confidence": round(t.confidence, 2),
                }
                for t in trains
            ],
        }

    @app.get("/history")
    async def get_history(
        since: str = Query(None, description="ISO datetime to query from"),
        limit: int = Query(50, ge=1, le=500),
        type: str = Query("intervals", description="'intervals' or 'passages'"),
    ):
        """Query historical crossing data."""
        if type == "passages":
            return {"passages": history.get_passages(since=since, limit=limit)}
        return {"intervals": history.get_intervals(since=since, limit=limit)}

    @app.get("/stats")
    async def stats():
        """Summary statistics."""
        return history.get_stats()

    @app.get("/next")
    async def next_trains(station: str = Query("ANG", description="CRS code (ANG or GBS)"),
                          limit: int = Query(5, ge=1, le=20)):
        """Upcoming trains at a station from RTT."""
        if not rtt_client:
            return {"error": "RTT not available"}
        return {"services": rtt_client.get_upcoming(station, limit)}

    @app.get("/up")
    async def up():
        """Minimal liveness probe for load balancers and uptime monitors."""
        return {"status": "ok"}

    @app.get("/health")
    async def health():
        """System health: uptime, feed status, DB size, tracked trains."""
        now = datetime.now(timezone.utc)
        uptime_secs = (now - _START_TIME).total_seconds()

        # Feed status
        feed_time = inferrer.status.last_feed_message
        feed_age = (now - feed_time).total_seconds() if feed_time else None
        stale_threshold = inferrer._timing.get("stale_threshold_secs", 300)

        # DB size
        db_path = Path(history.db_path)
        db_size_mb = round(db_path.stat().st_size / (1024 * 1024), 2) if db_path.exists() else 0

        # Train count
        with tracker._lock:
            total_trains = len(tracker.trains)
            active_count = sum(
                1 for t in tracker.trains.values()
                if t.phase.value not in ("cleared", "lost")
            )

        # RTT status
        rtt_info = {"available": rtt_client is not None}
        rtt_rate_limited = False
        if rtt_client is not None:
            rl = rtt_client.rate_limit_info
            rtt_info["rate_limited"] = rl["active"]
            if rl["active"]:
                rtt_rate_limited = True
                rtt_info["rate_limited_until"] = rl["until"]
                rtt_info["rate_limited_remaining_secs"] = rl["remaining_secs"]

        # Build warnings list
        feed_stale = feed_age is not None and feed_age > stale_threshold
        warnings = []
        if feed_stale:
            warnings.append("Network Rail feed is stale — crossing state may be outdated")
        if feed_age is None:
            warnings.append("Network Rail feed has not connected — no live data")
        if rtt_rate_limited:
            secs = rtt_info.get("rate_limited_remaining_secs", 0)
            warnings.append(f"Train schedule API is rate-limited — predictions may be unavailable for ~{secs}s")
        if rtt_client is None:
            warnings.append("Train schedule integration is not configured")

        overall = "healthy"
        if feed_stale or feed_age is None:
            overall = "degraded"
        elif rtt_rate_limited:
            overall = "degraded"

        return {
            "status": overall,
            "uptime_secs": round(uptime_secs),
            "started_at": _START_TIME.isoformat(),
            "feed": {
                "last_message": feed_time.isoformat() if feed_time else None,
                "age_secs": round(feed_age) if feed_age is not None else None,
                "stale": feed_stale,
            },
            "crossing_state": inferrer.status.state.value,
            "trains": {
                "active": active_count,
                "total_tracked": total_trains,
            },
            "db_size_mb": db_size_mb,
            "rtt": rtt_info,
            "warnings": warnings,
        }

    def _fetch_crossing_predictions(rtt, tracker_config):
        """Fetch RTT data from both stations and build per-train crossing ETAs."""
        now = datetime.now(timezone.utc)

        # Read crossing time offsets from config trust timing points
        trust_points = tracker_config.get("trust", {}).get("timing_points", [])
        offset_east = 90   # fallback
        offset_west = 120  # fallback
        for tp in trust_points:
            if tp.get("event") == "departure" and tp.get("action") == "predict":
                if tp.get("direction") == "up":
                    offset_east = tp.get("offset_secs", offset_east)
                elif tp.get("direction") == "down":
                    offset_west = tp.get("offset_secs", offset_west)

        # Fetch upcoming services from both stations
        ang_services = rtt.get_upcoming("ANG", 10)
        gbs_services = rtt.get_upcoming("GBS", 10)

        predictions = []
        seen = set()

        for svc in ang_services:
            # Eastbound trains at ANG haven't crossed yet
            if svc.get("direction") != "east":
                continue
            dep_iso = svc.get("departure_iso")
            if not dep_iso:
                continue
            dedup_key = f"{svc['headcode']}-{dep_iso}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            try:
                dep_time = _parse_rtt_time(dep_iso)
            except (ValueError, TypeError):
                continue
            crossing_eta = dep_time + timedelta(seconds=offset_east)
            if crossing_eta < now - timedelta(minutes=5):
                continue
            predictions.append({
                "headcode": svc["headcode"],
                "direction": "east",
                "crossing_eta": crossing_eta,
                "origin": svc.get("origin", ""),
                "destination": svc.get("destination", ""),
                "station": "ANG",
            })

        for svc in gbs_services:
            # Westbound trains at GBS haven't crossed yet
            if svc.get("direction") != "west":
                continue
            dep_iso = svc.get("departure_iso")
            if not dep_iso:
                continue
            dedup_key = f"{svc['headcode']}-{dep_iso}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            try:
                dep_time = _parse_rtt_time(dep_iso)
            except (ValueError, TypeError):
                continue
            crossing_eta = dep_time + timedelta(seconds=offset_west)
            if crossing_eta < now - timedelta(minutes=5):
                continue
            predictions.append({
                "headcode": svc["headcode"],
                "direction": "west",
                "crossing_eta": crossing_eta,
                "origin": svc.get("origin", ""),
                "destination": svc.get("destination", ""),
                "station": "GBS",
            })

        predictions.sort(key=lambda p: p["crossing_eta"])
        return predictions

    def _build_closure_windows(predictions, timing):
        """Build and merge closure windows from crossing predictions."""
        pre_closure = timing.get("pre_closure_secs", 120)
        crossing_clearance = timing.get("crossing_clearance_secs", 10)
        post_clearance = timing.get("post_clearance_secs", 5)

        merged = merge_closure_windows(
            predictions,
            pre_closure_secs=pre_closure,
            crossing_clearance_secs=crossing_clearance,
            post_clearance_secs=post_clearance,
        )

        # Format for the API response
        result_windows = []
        for w in merged:
            duration = (w["open_at"] - w["close_at"]).total_seconds()
            result_windows.append({
                "close_at": w["close_at"].isoformat(),
                "open_at": w["open_at"].isoformat(),
                "duration_secs": round(duration),
                "trains": [
                    {
                        "headcode": t["headcode"],
                        "direction": t["direction"],
                        "crossing_eta": t["crossing_eta"].astimezone(_GB_TZ).strftime("%H:%M"),
                        "origin": t["origin"],
                        "destination": t["destination"],
                    }
                    for t in w["trains"]
                ],
            })
        return result_windows

    @app.get("/predictions/windows")
    async def prediction_windows():
        """Upcoming crossing closure windows derived from RTT station data."""
        if not rtt_client:
            return {"windows": [], "generated_at": datetime.now(timezone.utc).isoformat(),
                    "error": "RTT not available"}

        now = datetime.now(timezone.utc)
        timing = tracker.config.get("timing", {})

        predictions = _fetch_crossing_predictions(rtt_client, tracker.config)
        result_windows = _build_closure_windows(predictions, timing)

        # Include current active closure from inferrer if present
        current_state = inferrer.status.state.value
        current_closure = None
        if current_state in ("closed_inferred", "closing_predicted", "opening_predicted"):
            active = inferrer.status.active_trains
            current_closure = {
                "state": current_state,
                "trains": [
                    {"headcode": t.headcode,
                     "direction": t.direction.value if t.direction else None}
                    for t in active
                ],
            }

        return {
            "windows": result_windows,
            "current_closure": current_closure,
            "generated_at": now.isoformat(),
        }

    def _format_human_delta(seconds: float) -> str:
        """Format seconds into a human-readable 'in X min' string."""
        if seconds < 0:
            return "now"
        if seconds < 60:
            return "less than 1 min"
        minutes = int(seconds / 60)
        if minutes < 60:
            return f"in {minutes} min"
        hours = minutes // 60
        remaining = minutes % 60
        if remaining == 0:
            return f"in {hours}h"
        return f"in {hours}h {remaining}m"

    def _direction_label(direction) -> str | None:
        """Normalize direction to east/west for public API."""
        if direction is None:
            return None
        d = direction if isinstance(direction, str) else direction.value
        return {"up": "east", "down": "west"}.get(d, d)

    @app.get("/predictions/next")
    async def prediction_next(limit: int = Query(5, ge=1, le=20)):
        """Upcoming crossing events — designed for notifications and HA integration.

        Returns a list of predicted state-change events (closing, opening)
        combining live train data with RTT schedule predictions.
        """
        now = datetime.now(timezone.utc)
        status = inferrer.status
        timing = tracker.config.get("timing", {})
        pre_closure = timing.get("pre_closure_secs", 120)
        crossing_clearance = timing.get("crossing_clearance_secs", 10)
        post_clearance = timing.get("post_clearance_secs", 5)

        # Build a unified list of closure windows by merging live + schedule

        # Step 1: Build a synthetic window for the current live closure (if any)
        live_window = None
        if status.state in (CrossingState.CLOSING_PREDICTED,
                            CrossingState.CLOSED_INFERRED,
                            CrossingState.OPENING_PREDICTED):
            live_close_at = status.since
            live_open_at = status.predicted_change
            if live_open_at is None:
                # No predicted opening — estimate from active trains
                latest_eta = max(
                    (t.predicted_at_crossing for t in status.active_trains
                     if t.predicted_at_crossing),
                    default=now,
                )
                live_open_at = latest_eta + timedelta(
                    seconds=crossing_clearance + post_clearance
                )
            live_window = {
                "close_at": live_close_at,
                "open_at": live_open_at,
                "trains": [
                    {
                        "headcode": t.headcode,
                        "direction": _direction_label(t.direction),
                    }
                    for t in status.active_trains
                ],
                "source": "live",
            }

        # Step 2: Fetch RTT schedule windows
        schedule_windows = []
        if rtt_client:
            predictions = _fetch_crossing_predictions(rtt_client, tracker.config)
            merged = merge_closure_windows(
                predictions,
                pre_closure_secs=pre_closure,
                crossing_clearance_secs=crossing_clearance,
                post_clearance_secs=post_clearance,
            )
            for w in merged:
                schedule_windows.append({
                    "close_at": w["close_at"],
                    "open_at": w["open_at"],
                    "trains": [
                        {
                            "headcode": t.get("headcode", ""),
                            "direction": _direction_label(t.get("direction")),
                        }
                        for t in w["trains"]
                    ],
                    "source": "schedule",
                })

        # Step 3: Merge live window with schedule windows
        all_windows = []
        if live_window:
            all_windows.append(live_window)
        for sw in schedule_windows:
            # Skip schedule windows that overlap with the live window
            if live_window and sw["close_at"] <= live_window["open_at"]:
                # Extend live window if the schedule window extends further
                if sw["open_at"] > live_window["open_at"]:
                    live_window["open_at"] = sw["open_at"]
                    live_window["trains"].extend(sw["trains"])
                continue
            all_windows.append(sw)

        # Step 4: Convert windows to events (closing/opening pairs)
        events = []
        for w in all_windows:
            close_secs = (w["close_at"] - now).total_seconds()
            open_secs = (w["open_at"] - now).total_seconds()

            # Only include future events (or currently-happening ones)
            if close_secs > -60:
                events.append({
                    "event": "closing",
                    "at": w["close_at"].isoformat(),
                    "in_seconds": max(0, round(close_secs)),
                    "in_human": _format_human_delta(close_secs),
                    "source": w["source"],
                    "confidence": 0.9 if w["source"] == "live" else 0.6,
                    "trains": w["trains"],
                })

            if open_secs > 0:
                events.append({
                    "event": "opening",
                    "at": w["open_at"].isoformat(),
                    "in_seconds": round(open_secs),
                    "in_human": _format_human_delta(open_secs),
                    "source": w["source"],
                    "confidence": 0.7 if w["source"] == "live" else 0.5,
                    "trains": [],
                })

        # Sort by time and limit
        events.sort(key=lambda e: e["at"])
        events = events[:limit]

        return {
            "current_state": status.state.value,
            "confidence": round(status.confidence, 2),
            "events": events,
            "generated_at": now.isoformat(),
        }

    @app.get("/sf/summary")
    async def sf_summary():
        """Summary of S-Class addresses: change counts, first/last seen."""
        return {"addresses": history.get_sf_summary()}

    @app.get("/sf")
    async def sf_events(
        since: str = Query(None, description="ISO datetime to query from"),
        address: str = Query(None, description="Filter by hex address"),
        limit: int = Query(100, ge=1, le=1000),
    ):
        """Recent S-Class signalling events."""
        return {"events": history.get_sf_events(since=since, address=address, limit=limit)}

    _ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

    def _check_admin(authorization: str = Header(None)):
        if not _ADMIN_TOKEN:
            raise HTTPException(503, "Admin token not configured")
        if not authorization or authorization != f"Bearer {_ADMIN_TOKEN}":
            raise HTTPException(401, "Unauthorized")

    class FeedbackBody(BaseModel):
        message: str = Field(..., min_length=1, max_length=2000)

    @app.post("/feedback")
    async def submit_feedback(body: FeedbackBody, request: Request):
        """Submit user feedback."""
        ua = request.headers.get("user-agent", "")
        fid = history.submit_feedback(body.message, user_agent=ua)
        return {"ok": True, "id": fid}

    @app.get("/feedback")
    async def get_feedback(limit: int = Query(50, ge=1, le=200),
                           _=Depends(_check_admin)):
        """Retrieve feedback submissions."""
        return {"feedback": history.get_feedback(limit=limit)}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
