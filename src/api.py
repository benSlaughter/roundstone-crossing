"""
API server — exposes crossing status, predictions, and history.
"""

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .inferrer import CrossingInferrer
from .history import HistoryLogger
from .tracker import TrainTracker

STATIC_DIR = Path(__file__).parent.parent / "static"


def create_app(tracker: TrainTracker, inferrer: CrossingInferrer, history: HistoryLogger) -> FastAPI:
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
        trains = []
        for hc, t in tracker.trains.items():
            if t.phase in (TrainPhase.LOST, TrainPhase.CLEARED):
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

    return app
