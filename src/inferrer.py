"""
Crossing state inferrer — derives crossing state from the set of active trains.
"""

import logging
from datetime import datetime, timedelta, timezone

from .models import CrossingState, CrossingStatus, TrainPhase, TrackedTrain

logger = logging.getLogger("crossing.inferrer")


class CrossingInferrer:
    """Infers crossing barrier state from tracked trains."""

    def __init__(self, config: dict):
        self.config = config
        self.status = CrossingStatus()
        self._timing = config.get("timing", {})

    def update(self, active_trains: list[TrackedTrain], last_feed_time: datetime | None):
        """Re-derive crossing state from the current set of active trains."""
        now = datetime.now(timezone.utc)
        old_state = self.status.state
        self.status.active_trains = active_trains
        self.status.last_feed_message = last_feed_time

        # Check for stale data
        stale_threshold = self._timing.get("stale_threshold_secs", 300)
        if last_feed_time and (now - last_feed_time).total_seconds() > stale_threshold:
            self._transition(CrossingState.STALE_DATA, confidence=0.3)
            return self.status

        if not active_trains:
            self._transition(CrossingState.OPEN, confidence=0.8)
            return self.status

        # Classify based on the "most advanced" train phase
        phases = [t.phase for t in active_trains]

        if TrainPhase.AT_CROSSING in phases:
            # Train is at the crossing — barriers almost certainly down
            self._transition(CrossingState.CLOSED_INFERRED, confidence=0.9)
            self.status.predicted_change = self._predict_opening(active_trains)
            self.status.predicted_next_state = CrossingState.OPENING_PREDICTED

        elif TrainPhase.STRIKE_IN in phases:
            # Train in strike-in zone — closure imminent
            nearest = self._nearest_train(active_trains, TrainPhase.STRIKE_IN)
            self._transition(CrossingState.CLOSING_PREDICTED, confidence=0.8)
            self.status.predicted_change = nearest.predicted_at_crossing if nearest else None
            self.status.predicted_next_state = CrossingState.CLOSED_INFERRED

        elif TrainPhase.APPROACHING in phases:
            # Train approaching but not yet in strike-in
            nearest = self._nearest_train(active_trains, TrainPhase.APPROACHING)
            pre_closure = self._timing.get("pre_closure_secs", 120)

            if nearest and nearest.predicted_at_crossing:
                secs_until = (nearest.predicted_at_crossing - now).total_seconds()
                if secs_until <= pre_closure:
                    # Close enough that barriers may be lowering
                    self._transition(CrossingState.CLOSING_PREDICTED, confidence=0.6)
                    barrier_close = nearest.predicted_at_crossing - timedelta(seconds=pre_closure)
                    self.status.predicted_change = max(barrier_close, now)
                    self.status.predicted_next_state = CrossingState.CLOSED_INFERRED
                else:
                    # Train visible but not imminent
                    self._transition(CrossingState.OPEN, confidence=0.7)
                    barrier_close = nearest.predicted_at_crossing - timedelta(seconds=pre_closure)
                    self.status.predicted_change = barrier_close
                    self.status.predicted_next_state = CrossingState.CLOSING_PREDICTED
            else:
                self._transition(CrossingState.OPEN, confidence=0.6)
        else:
            self._transition(CrossingState.OPEN, confidence=0.7)

        if self.status.state != old_state:
            logger.info(f"🚦 Crossing: {old_state.value} → {self.status.state.value} "
                        f"(confidence={self.status.confidence:.1%}, "
                        f"trains={len(active_trains)})")

        return self.status

    def _transition(self, new_state: CrossingState, confidence: float):
        """Change state if different, update timestamp."""
        if self.status.state != new_state:
            self.status.since = datetime.now(timezone.utc)
            self.status.predicted_change = None
            self.status.predicted_next_state = None
        self.status.state = new_state
        self.status.confidence = confidence

    def _nearest_train(self, trains: list[TrackedTrain], phase: TrainPhase) -> TrackedTrain | None:
        """Find the train in the given phase with the earliest predicted arrival."""
        candidates = [t for t in trains if t.phase == phase and t.predicted_at_crossing]
        if not candidates:
            return None
        return min(candidates, key=lambda t: t.predicted_at_crossing)

    def _predict_opening(self, trains: list[TrackedTrain]) -> datetime | None:
        """Predict when barriers will open — after the last train clears."""
        post_clearance = self._timing.get("post_clearance_secs", 15)
        # Assume train takes ~30s to clear the crossing from "at crossing"
        crossing_time = 30
        now = datetime.now(timezone.utc)
        return now + timedelta(seconds=crossing_time + post_clearance)
