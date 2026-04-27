"""
API server — exposes crossing status, predictions, and history.
"""

from datetime import datetime

from fastapi import FastAPI, Query

from .inferrer import CrossingInferrer
from .history import HistoryLogger


def create_app(inferrer: CrossingInferrer, history: HistoryLogger) -> FastAPI:
    app = FastAPI(title="Roundstone Crossing Predictor")

    @app.get("/status")
    async def status():
        """Current inferred crossing state."""
        return inferrer.status.to_dict()

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
