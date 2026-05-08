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
        self._inference = config.get("inference", {})
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
                          Will be ignored entirely if `inference.use_routes` is False.
        """
        now = datetime.now(timezone.utc)
        old_state = self.status.state
        self.status.active_trains = active_trains
        self.status.last_feed_message = last_feed_time

        # Honour the inference.use_routes config flag. When False, routes are
        # ignored for state derivation entirely (they are still observed,
        # logged to sf_events and shown on /live — this only affects the
        # state machine). Default False after production regression where
        # route-based inference reported CLOSED while barriers were OPEN.
        if not self._inference.get("use_routes", False):
            active_routes = None

        routes = active_routes or []
        has_routes = len(routes) > 0

        # Check for stale data
        stale_threshold = self._timing.get("stale_threshold_secs", 300)
        if last_feed_time and (now - last_feed_time).total_seconds() > stale_threshold:
            age = (now - last_feed_time).total_seconds()
            self._transition(CrossingState.STALE_DATA, confidence=0.3,
                             reason=f"feed silent for {age:.0f}s (threshold {stale_threshold}s)")
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
                    self._transition(
                        CrossingState.UNKNOWN, confidence=0.3,
                        reason=f"route hold timeout ({held:.0f}s > {max_hold}s) "
                               f"with no train activity — routes probably stuck "
                               f"(active: {','.join(sorted(routes))})",
                    )
                    return self.status

                # Track _last_clear_time so OPENING_PREDICTED fires briefly when
                # the route eventually clears (signaller verifies CCTV before
                # raising barriers — same flow as a normal post-train clearance).
                self._last_clear_time = now
                self._transition(
                    CrossingState.CLOSING_PREDICTED, confidence=0.7,
                    reason=f"route SET, no train in zone yet "
                           f"({','.join(sorted(routes))}) — early warning",
                )
                return self.status

            # No trains AND no routes — leaving any route-only state.
            self._routes_only_since = None

            # Check if we just cleared — show OPENING_PREDICTED briefly
            post_clearance = self._timing.get("post_clearance_secs", 15)
            if (self._last_clear_time
                    and (now - self._last_clear_time).total_seconds() < post_clearance):
                self._transition(
                    CrossingState.OPENING_PREDICTED, confidence=0.8,
                    reason=f"all trains/routes cleared — post-clearance window "
                           f"({post_clearance}s, signaller verifying CCTV)",
                )
                open_at = self._last_clear_time + timedelta(seconds=post_clearance)
                self.status.predicted_change = open_at
                self.status.predicted_next_state = CrossingState.OPEN
            else:
                self._transition(CrossingState.OPEN, confidence=0.8,
                                 reason="no trains in zone, no routes set")
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
            at_xng = [t.headcode for t in active_trains if t.phase == TrainPhase.AT_CROSSING]
            route_str = f" + routes ({','.join(sorted(routes))})" if has_routes else ""
            self._transition(
                CrossingState.CLOSED_INFERRED, confidence=confidence,
                reason=f"train at crossing: {','.join(at_xng)}{route_str}",
            )
            self._last_clear_time = now
            self.status.predicted_change = self._predict_opening(active_trains)
            self.status.predicted_next_state = CrossingState.OPENING_PREDICTED

        elif was_closed:
            # Barriers already down — keep them down while trains or routes remain active
            confidence = 0.9 if has_routes else 0.85
            train_count = len(active_trains)
            route_str = f", {len(routes)} route(s) still SET" if has_routes else ""
            self._transition(
                CrossingState.CLOSED_INFERRED, confidence=confidence,
                reason=f"barriers held closed — {train_count} train(s) "
                       f"still active{route_str}",
            )
            self._last_clear_time = now
            self.status.predicted_change = self._predict_opening(active_trains)
            self.status.predicted_next_state = CrossingState.OPENING_PREDICTED

        elif (TrainPhase.STRIKE_IN in phases or TrainPhase.AT_STATION in phases) and has_routes:
            # Strike-in/station AND route SET — barriers ARE down (MCB-CCTV procedure)
            nearest = (self._nearest_train(active_trains, TrainPhase.STRIKE_IN)
                       or self._nearest_train(active_trains, TrainPhase.AT_STATION))
            hc = nearest.headcode if nearest else "?"
            phase_label = "strike-in" if TrainPhase.STRIKE_IN in phases else "at-station"
            self._transition(
                CrossingState.CLOSED_INFERRED, confidence=0.9,
                reason=f"{phase_label} train ({hc}) + route SET "
                       f"({','.join(sorted(routes))}) — MCB-CCTV procedure "
                       f"requires barriers down before route can be set",
            )
            self._last_clear_time = now
            self.status.predicted_change = self._predict_opening(active_trains)
            self.status.predicted_next_state = CrossingState.OPENING_PREDICTED

        elif TrainPhase.STRIKE_IN in phases or TrainPhase.AT_STATION in phases:
            # Strike-in without route data — same as before
            nearest = (self._nearest_train(active_trains, TrainPhase.STRIKE_IN)
                       or self._nearest_train(active_trains, TrainPhase.AT_STATION))
            hc = nearest.headcode if nearest else "?"
            phase_label = "strike-in" if TrainPhase.STRIKE_IN in phases else "at-station"
            self._transition(
                CrossingState.CLOSING_PREDICTED, confidence=0.8,
                reason=f"{phase_label} train ({hc}) but no route confirmation",
            )
            self.status.predicted_change = nearest.predicted_at_crossing if nearest else None
            self.status.predicted_next_state = CrossingState.CLOSED_INFERRED

        elif TrainPhase.APPROACHING in phases and has_routes:
            # Approaching train with route SET — barriers likely lowering already
            nearest = self._nearest_train(active_trains, TrainPhase.APPROACHING)
            hc = nearest.headcode if nearest else "?"
            self._transition(
                CrossingState.CLOSING_PREDICTED, confidence=0.85,
                reason=f"approaching train ({hc}) + route SET "
                       f"({','.join(sorted(routes))}) — barriers likely down",
            )
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
            route_str = f", routes={len(routes)}" if routes else ""
            logger.info(f"Crossing: {old_state.value} -> {self.status.state.value} "
                        f"(confidence={self.status.confidence:.1%}, "
                        f"trains={len(active_trains)}{route_str}) "
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
