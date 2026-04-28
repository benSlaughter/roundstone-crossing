"""
API server — exposes crossing status, predictions, health, and history.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .inferrer import CrossingInferrer
from .history import HistoryLogger
from .tracker import TrainTracker

STATIC_DIR = Path(__file__).parent.parent / "static"
_START_TIME = datetime.now(timezone.utc)


def create_app(tracker: TrainTracker, inferrer: CrossingInferrer, history: HistoryLogger,
               rtt_client=None) -> FastAPI:
    app = FastAPI(title="Roundstone Crossing Predictor")

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

        return {
            "status": "healthy" if feed_age is not None and feed_age < stale_threshold else "degraded",
            "uptime_secs": round(uptime_secs),
            "started_at": _START_TIME.isoformat(),
            "feed": {
                "last_message": feed_time.isoformat() if feed_time else None,
                "age_secs": round(feed_age) if feed_age is not None else None,
                "stale": feed_age is not None and feed_age > stale_threshold,
            },
            "crossing_state": inferrer.status.state.value,
            "trains": {
                "active": active_count,
                "total_tracked": total_trains,
            },
            "db_size_mb": db_size_mb,
            "rtt_available": rtt_client is not None,
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

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
