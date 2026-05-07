"""
Crossing state inferrer — derives crossing state from the set of active trains.
"""

import logging
from datetime import datetime, timedelta, timezone

from .models import CrossingState, CrossingStatus, TrainPhase, TrackedTrain
from .utils import merge_closure_windows

logger = logging.getLogger("crossing.inferrer")


class CrossingInferrer:
    """Infers crossing barrier state from tracked trains and route data."""

    def __init__(self, config: dict):
        self.config = config
        self.status = CrossingStatus()
        self._timing = config.get("timing", {})
        self._last_clear_time: datetime | None = None
        # Tracks when the route-only "barriers down" inference began. Used to
        # cap that inference (stuck routes shouldn't keep us locked in CLOSED
        # forever) and to support OPENING_PREDICTED when the route eventually
        # clears with no train ever appearing in our berth zone.
        self._routes_only_since: datetime | None = None

    def update(self, active_trains: list[TrackedTrain], last_feed_time: datetime | None,
               active_routes: list[str] | None = None):
        """Re-derive crossing state from the current set of active trains and route state.

        Args:
            active_trains: Currently tracked trains near the crossing.
            last_feed_time: Timestamp of last received feed message.
            active_routes: List of crossing route names currently SET (from RouteMonitor).
                          None means route data unavailable (backward compat).
        """
        now = datetime.now(timezone.utc)
        old_state = self.status.state
        self.status.active_trains = active_trains
        self.status.last_feed_message = last_feed_time
        routes = active_routes or []
        has_routes = len(routes) > 0

        # Check for stale data
        stale_threshold = self._timing.get("stale_threshold_secs", 300)
        if last_feed_time and (now - last_feed_time).total_seconds() > stale_threshold:
            self._transition(CrossingState.STALE_DATA, confidence=0.3)
            return self.status

        if not active_trains:
            if has_routes:
                # No trains visible but routes are still SET — barriers likely still down
                # This catches the gap between route SET and train appearing in our zone.
                if self._routes_only_since is None:
                    self._routes_only_since = now
                held = (now - self._routes_only_since).total_seconds()

                # Cap the "barriers down" inference: if routes have been SET for
                # too long with no train activity, the route is probably stuck
                # (240s lock-after-cancel, signal failure, or signaller anomaly).
                # Admit uncertainty rather than assert CLOSED indefinitely.
                max_hold = self._timing.get("max_route_hold_secs", 900)
                if held > max_hold:
                    self._transition(CrossingState.UNKNOWN, confidence=0.3)
                    return self.status

                # Track _last_clear_time so OPENING_PREDICTED fires briefly when
                # the route eventually clears (signaller verifies CCTV before
                # raising barriers — same flow as a normal post-train clearance).
                self._last_clear_time = now
                self._transition(CrossingState.CLOSING_PREDICTED, confidence=0.7)
                return self.status

            # No trains AND no routes — leaving any route-only state.
            self._routes_only_since = None

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

        # Trains active — leaving any route-only state.
        self._routes_only_since = None

        # Classify based on the "most advanced" train phase
        phases = [t.phase for t in active_trains]

        # Once barriers are closed, they stay closed until ALL trains clear.
        # The only valid exit from CLOSED is → OPENING_PREDICTED → OPEN.
        was_closed = old_state in (CrossingState.CLOSED_INFERRED,
                                   CrossingState.OPENING_PREDICTED)

        if TrainPhase.AT_CROSSING in phases:
            # Train is at the crossing — barriers almost certainly down
            confidence = 0.95 if has_routes else 0.9
            self._transition(CrossingState.CLOSED_INFERRED, confidence=confidence)
            self._last_clear_time = now
            self.status.predicted_change = self._predict_opening(active_trains)
            self.status.predicted_next_state = CrossingState.OPENING_PREDICTED

        elif was_closed:
            # Barriers already down — keep them down while trains or routes remain active
            confidence = 0.9 if has_routes else 0.85
            self._transition(CrossingState.CLOSED_INFERRED, confidence=confidence)
            self._last_clear_time = now
            self.status.predicted_change = self._predict_opening(active_trains)
            self.status.predicted_next_state = CrossingState.OPENING_PREDICTED

        elif (TrainPhase.STRIKE_IN in phases or TrainPhase.AT_STATION in phases) and has_routes:
            # Strike-in/station AND route SET — barriers ARE down (MCB-CCTV procedure)
            self._transition(CrossingState.CLOSED_INFERRED, confidence=0.9)
            self._last_clear_time = now
            nearest = (self._nearest_train(active_trains, TrainPhase.STRIKE_IN)
                       or self._nearest_train(active_trains, TrainPhase.AT_STATION))
            self.status.predicted_change = self._predict_opening(active_trains)
            self.status.predicted_next_state = CrossingState.OPENING_PREDICTED

        elif TrainPhase.STRIKE_IN in phases or TrainPhase.AT_STATION in phases:
            # Strike-in without route data — same as before
            nearest = (self._nearest_train(active_trains, TrainPhase.STRIKE_IN)
                       or self._nearest_train(active_trains, TrainPhase.AT_STATION))
            self._transition(CrossingState.CLOSING_PREDICTED, confidence=0.8)
            self.status.predicted_change = nearest.predicted_at_crossing if nearest else None
            self.status.predicted_next_state = CrossingState.CLOSED_INFERRED

        elif TrainPhase.APPROACHING in phases and has_routes:
            # Approaching train with route SET — barriers likely lowering already
            nearest = self._nearest_train(active_trains, TrainPhase.APPROACHING)
            self._transition(CrossingState.CLOSING_PREDICTED, confidence=0.85)
            self.status.predicted_change = nearest.predicted_at_crossing if nearest else None
            self.status.predicted_next_state = CrossingState.CLOSED_INFERRED

        elif TrainPhase.APPROACHING in phases:
            # Train approaching but not yet in strike-in (no route info)
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
            route_str = f", routes={len(routes)}" if routes else ""
            logger.info(f"Crossing: {old_state.value} -> {self.status.state.value} "
                        f"(confidence={self.status.confidence:.1%}, "
                        f"trains={len(active_trains)}{route_str})")

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

        # Build prediction dicts for non-cleared trains
        predictions: list[dict] = []
        for t in trains:
            if t.phase in (TrainPhase.CLEARED, TrainPhase.LOST):
                continue
            eta = self._estimate_clear_time(t, now, crossing_clear)
            if eta is None:
                continue
            # Clamp to 'now' so windows don't extend into the past
            clamped_eta = max(eta, now)
            predictions.append({"crossing_eta": clamped_eta})

        if not predictions:
            return now + timedelta(seconds=crossing_clear + post_clearance)

        # Use pre_closure + crossing_clear as the effective pre-closure
        # because _estimate_clear_time already adds crossing_clear to the ETA
        merged = merge_closure_windows(
            predictions,
            pre_closure_secs=crossing_clear + pre_closure,
            crossing_clearance_secs=0,
            post_clearance_secs=post_clearance,
        )

        # Return the end of the window containing 'now', or the first window
        for w in merged:
            if w["close_at"] <= now <= w["open_at"]:
                return w["open_at"]
        return merged[0]["open_at"]

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
