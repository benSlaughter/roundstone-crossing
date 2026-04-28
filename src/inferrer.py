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
        self._last_clear_time: datetime | None = None

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
            # Check if we just cleared — show OPENING_PREDICTED briefly
            post_clearance = self._timing.get("post_clearance_secs", 15)
            if (self._last_clear_time
                    and (now - self._last_clear_time).total_seconds() < post_clearance):
                self._transition(CrossingState.OPENING_PREDICTED, confidence=0.8)
                open_at = self._last_clear_time + timedelta(seconds=post_clearance)
                self.status.predicted_change = open_at
                self.status.predicted_next_state = CrossingState.OPEN
            else:
                self._transition(CrossingState.OPEN, confidence=0.8)
                self._last_clear_time = None
            return self.status

        # Track when trains were last present (for OPENING_PREDICTED after they clear)
        self._last_clear_time = now

        # Classify based on the "most advanced" train phase
        phases = [t.phase for t in active_trains]

        if TrainPhase.AT_CROSSING in phases:
            # Train is at the crossing — barriers almost certainly down
            self._transition(CrossingState.CLOSED_INFERRED, confidence=0.9)
            self.status.predicted_change = self._predict_opening(active_trains)
            self.status.predicted_next_state = CrossingState.OPENING_PREDICTED

        elif TrainPhase.STRIKE_IN in phases or TrainPhase.AT_STATION in phases:
            # Train in strike-in zone or at a station before the crossing
            nearest = (self._nearest_train(active_trains, TrainPhase.STRIKE_IN)
                       or self._nearest_train(active_trains, TrainPhase.AT_STATION))
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
            logger.info(f"Crossing: {old_state.value} -> {self.status.state.value} "
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
        """Predict when barriers will open based on closure window merging.

        Builds a closure window per train and merges overlapping windows.
        The predicted opening is the end of the current merged closure block.
        Trains without a credible predicted_at_crossing are estimated by phase.
        """
        now = datetime.now(timezone.utc)
        crossing_clear = self._timing.get("crossing_clearance_secs", 30)
        post_clearance = self._timing.get("post_clearance_secs", 15)
        pre_closure = self._timing.get("pre_closure_secs", 120)

        # Build closure windows: (start, end) for each non-cleared train
        windows: list[tuple[datetime, datetime]] = []
        for t in trains:
            if t.phase in (TrainPhase.CLEARED, TrainPhase.LOST):
                continue
            eta = self._estimate_clear_time(t, now, crossing_clear)
            if eta is None:
                continue
            # Window starts when barriers would lower for this train
            window_start = max(eta - timedelta(seconds=crossing_clear + pre_closure), now)
            window_end = max(eta + timedelta(seconds=post_clearance), now)
            windows.append((window_start, window_end))

        if not windows:
            return now + timedelta(seconds=crossing_clear + post_clearance)

        # Merge overlapping windows, find the one containing 'now'
        windows.sort()
        merged: list[tuple[datetime, datetime]] = [windows[0]]
        for start, end in windows[1:]:
            if start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        # Return the end of the current (or first) merged block
        for start, end in merged:
            if start <= now <= end:
                return end
        return merged[0][1]

    def _estimate_clear_time(self, train: TrackedTrain, now: datetime,
                             crossing_clear_secs: float) -> datetime | None:
        """Estimate when a train will have cleared the crossing.

        Uses predicted_at_crossing when available; falls back to phase-based
        heuristics for AT_CROSSING trains only (other phases without an ETA
        are excluded to avoid false certainty).
        """
        if train.phase == TrainPhase.AT_CROSSING:
            # Train is physically at the crossing — use crossing clearance time
            base = train.predicted_at_crossing or now
            return max(base, now) + timedelta(seconds=crossing_clear_secs)

        if train.predicted_at_crossing:
            # Have a credible ETA — clearing = arrival + crossing time
            return max(train.predicted_at_crossing, now) + timedelta(seconds=crossing_clear_secs)

        # No predicted_at_crossing and not AT_CROSSING — exclude from prediction
        # to avoid inventing unreliable ETAs
        return None
