"""
Crossing state inferrer — derives crossing state from the set of active trains.

NOTE: Route data (SF/SG signalling) is intentionally NOT used for prediction.
The `active_routes` parameter on `update()` is accepted for backward
compatibility with callers and tests but is unconditionally ignored. Routes
are still observed by `RouteMonitor` and exposed via the API + history table
for diagnostic purposes — they just cannot influence inferred state. See
PR / commit history for the 2026-05-08 production regression that motivated
removing route-based inference.
"""

import logging
from datetime import datetime, timedelta, timezone

from .models import CrossingState, CrossingStatus, TrainPhase, TrackedTrain
from .utils import merge_closure_windows

logger = logging.getLogger("crossing.inferrer")


class CrossingInferrer:
    """Infers crossing barrier state from tracked trains.

    Route data is intentionally not consumed (see module docstring).
    """

    def __init__(self, config: dict):
        self.config = config
        self.status = CrossingStatus()
        self._timing = config.get("timing", {})
        self._last_clear_time: datetime | None = None

    def update(self, active_trains: list[TrackedTrain], last_feed_time: datetime | None,
               active_routes: list[str] | None = None):
        """Re-derive crossing state from the current set of active trains.

        Args:
            active_trains: Currently tracked trains near the crossing.
            last_feed_time: Timestamp of last received feed message.
            active_routes: IGNORED. Accepted for backward compatibility only.
                Route observation has been deliberately decoupled from
                prediction — see module docstring.
        """
        # Route data is unconditionally discarded before any state logic
        # runs. Tests parametrise across a range of `active_routes` values
        # and assert the inferrer's output is identical regardless.
        del active_routes

        now = datetime.now(timezone.utc)
        old_state = self.status.state
        self.status.active_trains = active_trains
        self.status.last_feed_message = last_feed_time

        # Check for stale data
        stale_threshold = self._timing.get("stale_threshold_secs", 300)
        if last_feed_time and (now - last_feed_time).total_seconds() > stale_threshold:
            age = (now - last_feed_time).total_seconds()
            self._transition(CrossingState.STALE_DATA, confidence=0.3,
                             reason=f"feed silent for {age:.0f}s (threshold {stale_threshold}s)")
            return self.status

        if not active_trains:
            # Check if we just cleared — show OPENING_PREDICTED briefly
            post_clearance = self._timing.get("post_clearance_secs", 15)
            if (self._last_clear_time
                    and (now - self._last_clear_time).total_seconds() < post_clearance):
                self._transition(
                    CrossingState.OPENING_PREDICTED, confidence=0.8,
                    reason=f"all trains cleared — post-clearance window "
                           f"({post_clearance}s, signaller verifying CCTV)",
                )
                open_at = self._last_clear_time + timedelta(seconds=post_clearance)
                self.status.predicted_change = open_at
                self.status.predicted_next_state = CrossingState.OPEN
            else:
                self._transition(CrossingState.OPEN, confidence=0.8,
                                 reason="no trains in zone")
                self._last_clear_time = None
            return self.status

        # Classify based on the "most advanced" train phase
        phases = [t.phase for t in active_trains]

        # Once barriers are closed, they stay closed until ALL trains clear.
        # The only valid exit from CLOSED is → OPENING_PREDICTED → OPEN.
        was_closed = old_state in (CrossingState.CLOSED_INFERRED,
                                   CrossingState.OPENING_PREDICTED)

        if TrainPhase.AT_CROSSING in phases:
            # Train is at the crossing — barriers almost certainly down
            at_xng = [t.headcode for t in active_trains if t.phase == TrainPhase.AT_CROSSING]
            self._transition(
                CrossingState.CLOSED_INFERRED, confidence=0.9,
                reason=f"train at crossing: {','.join(at_xng)}",
            )
            self._last_clear_time = now
            self.status.predicted_change = self._predict_opening(active_trains)
            self.status.predicted_next_state = CrossingState.OPENING_PREDICTED

        elif was_closed and self._has_train_holding_barriers(active_trains, now):
            # Barriers held closed by an imminent or at-the-crossing train.
            # Approaching trains beyond `pre_closure_secs` away no longer
            # count — they're tomorrow's closure, not the current one. This
            # stops the predictor from staying CLOSED for minutes after the
            # last train cleared just because a far-future train was
            # tracked in the approach zone.
            train_count = len(active_trains)
            self._transition(
                CrossingState.CLOSED_INFERRED, confidence=0.85,
                reason=f"barriers held closed — {train_count} train(s) still active",
            )
            self._last_clear_time = now
            self.status.predicted_change = self._predict_opening(active_trains)
            self.status.predicted_next_state = CrossingState.OPENING_PREDICTED

        elif TrainPhase.STRIKE_IN in phases or TrainPhase.AT_STATION in phases:
            # Strike-in: barriers likely lowering / about to lower
            nearest = (self._nearest_train(active_trains, TrainPhase.STRIKE_IN)
                       or self._nearest_train(active_trains, TrainPhase.AT_STATION))
            hc = nearest.headcode if nearest else "?"
            phase_label = "strike-in" if TrainPhase.STRIKE_IN in phases else "at-station"
            self._transition(
                CrossingState.CLOSING_PREDICTED, confidence=0.8,
                reason=f"{phase_label} train ({hc}) — predicting closure",
            )
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
                    self._transition(
                        CrossingState.CLOSING_PREDICTED, confidence=0.6,
                        reason=f"approaching train ({nearest.headcode}) "
                               f"~{secs_until:.0f}s out, within pre_closure "
                               f"window ({pre_closure}s)",
                    )
                    barrier_close = nearest.predicted_at_crossing - timedelta(seconds=pre_closure)
                    self.status.predicted_change = max(barrier_close, now)
                    self.status.predicted_next_state = CrossingState.CLOSED_INFERRED
                else:
                    # Train visible but not imminent
                    self._transition(
                        CrossingState.OPEN, confidence=0.7,
                        reason=f"approaching train ({nearest.headcode}) "
                               f"~{secs_until:.0f}s out — outside pre_closure "
                               f"window ({pre_closure}s)",
                    )
                    barrier_close = nearest.predicted_at_crossing - timedelta(seconds=pre_closure)
                    self.status.predicted_change = barrier_close
                    self.status.predicted_next_state = CrossingState.CLOSING_PREDICTED
            else:
                self._transition(
                    CrossingState.OPEN, confidence=0.6,
                    reason=f"approaching train(s) {[t.headcode for t in active_trains]} "
                           f"with no ETA — assuming far",
                )
        else:
            # Trains active but none in any classified phase (e.g. all CLEARED but
            # not yet pruned). Treat as open.
            self._transition(CrossingState.OPEN, confidence=0.7,
                             reason="no train in approach/strike-in/at-crossing phase")

        if self.status.state != old_state:
            logger.info(f"Crossing: {old_state.value} -> {self.status.state.value} "
                        f"(confidence={self.status.confidence:.1%}, "
                        f"trains={len(active_trains)}) "
                        f"reason: {self.status.reason}")

        return self.status

    def _transition(self, new_state: CrossingState, confidence: float, reason: str | None = None):
        """Change state if different, update timestamp, record reason.

        The `reason` is set on every call (so it always reflects the most recent
        rationale), but `since`/`predicted_change`/`predicted_next_state` only
        reset when the state itself changes.
        """
        if self.status.state != new_state:
            self.status.since = datetime.now(timezone.utc)
            self.status.predicted_change = None
            self.status.predicted_next_state = None
        self.status.state = new_state
        self.status.confidence = confidence
        self.status.reason = reason

    def _nearest_train(self, trains: list[TrackedTrain], phase: TrainPhase) -> TrackedTrain | None:
        """Find the train in the given phase with the earliest predicted arrival."""
        candidates = [t for t in trains if t.phase == phase and t.predicted_at_crossing]
        if not candidates:
            return None
        return min(candidates, key=lambda t: t.predicted_at_crossing)

    def _has_train_holding_barriers(self, trains: list[TrackedTrain],
                                    now: datetime) -> bool:
        """True if at least one train justifies keeping barriers down right now.

        A train holds the barriers down if it is:
          * AT_CROSSING — physically on the crossing
          * STRIKE_IN — past the strike-in signal; under MCB-CCTV the
            signaller must have lowered barriers before this
          * AT_STATION — train dwelling at a platform that may itself be
            at the crossing (e.g. Angmering down) or imminently leaving
          * APPROACHING with a predicted at-crossing time within
            `pre_closure_secs` from now — close enough that barriers
            would not raise and re-close in time

        An APPROACHING train more than `pre_closure_secs` away does NOT
        count: barriers physically have time to raise and re-close
        before that train reaches the crossing, and the camera-ground-
        truth analysis (2026-05-14) shows they typically do.
        """
        pre_closure = self._timing.get("pre_closure_secs", 120)
        for t in trains:
            if t.phase in (TrainPhase.AT_CROSSING, TrainPhase.STRIKE_IN,
                           TrainPhase.AT_STATION):
                return True
            if t.phase == TrainPhase.APPROACHING and t.predicted_at_crossing:
                secs_until = (t.predicted_at_crossing - now).total_seconds()
                if secs_until <= pre_closure:
                    return True
        return False

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
