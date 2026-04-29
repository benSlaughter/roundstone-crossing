"""
Train tracker — maintains per-train objects from TD and TRUST messages.
Correlates TD berth steps with TRUST train identities.
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from .models import TrackedTrain, TrainPhase, Direction

logger = logging.getLogger("crossing.tracker")


class TrainTracker:
    """Tracks trains approaching/passing the crossing based on TD and TRUST data."""

    def __init__(self, config: dict, history=None):
        self.config = config
        self.history = history
        self.trains: dict[str, TrackedTrain] = {}  # keyed by headcode
        self._lock = threading.Lock()
        self._load_berth_zones()
        self._load_station_berths()

    def _load_berth_zones(self):
        """Load berth zone config — which berths map to which crossing phase."""
        td = self.config.get("td", {})
        self.area_id = td.get("area_id", "ES")

        self.approach_berths = {
            Direction.UP: set(td.get("approach_berths", {}).get("up", [])),
            Direction.DOWN: set(td.get("approach_berths", {}).get("down", [])),
        }
        self.strike_in_berths = {
            Direction.UP: set(td.get("strike_in_berths", {}).get("up", [])),
            Direction.DOWN: set(td.get("strike_in_berths", {}).get("down", [])),
        }
        self.at_crossing_berths = {
            Direction.UP: set(td.get("at_crossing_berths", {}).get("up", [])),
            Direction.DOWN: set(td.get("at_crossing_berths", {}).get("down", [])),
        }
        self.clear_berths = {
            Direction.UP: set(td.get("clear_berths", {}).get("up", [])),
            Direction.DOWN: set(td.get("clear_berths", {}).get("down", [])),
        }

        # All berths we care about (for filtering the TD feed)
        self.all_relevant_berths = set()
        for d in Direction:
            self.all_relevant_berths |= self.approach_berths[d]
            self.all_relevant_berths |= self.strike_in_berths[d]
            self.all_relevant_berths |= self.at_crossing_berths[d]
            self.all_relevant_berths |= self.clear_berths[d]

        if not self.all_relevant_berths:
            logger.warning("No berth zones configured — TD tracking will not work until berths are mapped")

    def _load_station_berths(self):
        """Load station berth config — berths that straddle station platforms."""
        self.station_berths = self.config.get("station_berths", {})
        # Add station berths to relevant set so TD feed picks them up
        for berth in self.station_berths:
            self.all_relevant_berths.add(berth)

    def handle_td_step(self, from_berth: str, to_berth: str, headcode: str, timestamp: datetime):
        """Process a TD berth step message. Updates the tracked train's position."""
        with self._lock:
            if not headcode or headcode.strip() == "":
                return

            # Only care about berths near our crossing
            if to_berth not in self.all_relevant_berths and from_berth not in self.all_relevant_berths:
                return

            now = timestamp or datetime.now(timezone.utc)

            # Get or create tracked train
            train = self.trains.get(headcode)
            if not train:
                train = TrackedTrain(headcode=headcode, last_berth=to_berth, last_berth_time=now)
                self.trains[headcode] = train
                logger.info(f"New train spotted: {headcode} at berth {to_berth}")

            train.last_berth = to_berth
            train.last_berth_time = now
            train.last_update = now

            # Clear sub_position when leaving a station berth
            if from_berth in self.station_berths and to_berth not in self.station_berths:
                train.sub_position = None

            # Set sub_position when entering a station berth
            if to_berth in self.station_berths:
                sb = self.station_berths[to_berth]
                train.sub_position = "entry"
                train.station = sb.get("station")

            # If train has no direction yet, try to infer from from_berth
            hint_direction = train.direction
            if not hint_direction and from_berth:
                _, from_dir = self._classify_berth(from_berth)
                if from_dir:
                    hint_direction = from_dir

            # Determine direction and phase from the berth
            new_phase, direction = self._classify_berth(to_berth, hint_direction)

            # Don't allow phase regressions (e.g. cleared/at_crossing → approaching)
            phase_order = {
                TrainPhase.APPROACHING: 0, TrainPhase.STRIKE_IN: 1,
                TrainPhase.AT_STATION: 1,  # Same level as strike_in (station is within that zone)
                TrainPhase.AT_CROSSING: 2, TrainPhase.CLEARED: 3,
            }
            if (new_phase and train.phase and
                    phase_order.get(new_phase, 0) < phase_order.get(train.phase, 0)):
                return

            if direction:
                train.direction = direction
            if new_phase:
                if new_phase != train.phase:
                    logger.info(f"{headcode} ({train.direction.value if train.direction else '?'}): "
                                f"{train.phase.value} → {new_phase.value} (berth {to_berth})")
                train.phase = new_phase

            # Log berth step for calibration data
            if self.history:
                self.history.log_train_event(
                    headcode=headcode,
                    event="step",
                    from_berth=from_berth,
                    to_berth=to_berth,
                    phase=train.phase.value if train.phase else None,
                    direction=train.direction.value if train.direction else None,
                )

            # Predict when train will be at crossing
            if new_phase in (TrainPhase.APPROACHING, TrainPhase.STRIKE_IN):
                train.predicted_at_crossing = self._estimate_arrival(train)
                train.confidence = 0.7 if new_phase == TrainPhase.STRIKE_IN else 0.5

            elif new_phase == TrainPhase.AT_CROSSING:
                train.predicted_at_crossing = now
                train.confidence = 0.9

            elif new_phase == TrainPhase.CLEARED:
                train.confidence = 0.9

    def handle_td_cancel(self, berth: str, headcode: str, timestamp: datetime):
        """Handle a TD berth cancel (CB_MSG). Removes train if it matches the cancelled berth."""
        with self._lock:
            if berth not in self.all_relevant_berths:
                return

            train = self.trains.get(headcode)
            if not train:
                return

            # Only act if the cancel is for the berth the train is currently in
            if train.last_berth == berth:
                logger.info(f"{headcode}: berth cancel at {berth} → lost")
                train.phase = TrainPhase.LOST
                train.last_update = timestamp or datetime.now(timezone.utc)

    def handle_trust_movement(self, train_id: str, stanox: str, event_type: str,
                               actual_time: datetime, headcode: Optional[str] = None):
        """Process a TRUST train movement message. Used for identity, timing, and clearing."""
        with self._lock:
            trust_config = self.config.get("trust", {}).get("timing_points", [])

            # Match on STANOX and event type (arrival/departure)
            trust_event = "arrival" if event_type == "ARRIVAL" else "departure"
            matching = [tp for tp in trust_config
                        if tp.get("stanox") == stanox and tp.get("event", "departure") == trust_event]
            if not matching:
                return

            hc = headcode or (train_id[2:6] if len(train_id) >= 6 else train_id[:4])
            if not hc:
                return

            # If train already tracked with a known direction, prefer the matching config entry
            existing = self.trains.get(hc)
            if existing and existing.direction and len(matching) > 1:
                directed = [tp for tp in matching if tp.get("direction") == existing.direction.value]
                tp = directed[0] if directed else matching[0]
            else:
                tp = matching[0]

            direction = Direction(tp["direction"])
            action = tp.get("action", "predict")
            station = tp.get("station", tp.get("tiploc", ""))

            # Get or create train
            train = self.trains.get(hc)
            if train:
                train.train_id = train_id
                train.last_update = actual_time
            else:
                train = TrackedTrain(
                    headcode=hc,
                    train_id=train_id,
                    direction=direction,
                    phase=TrainPhase.APPROACHING,
                    confidence=0.4,
                    last_update=actual_time,
                )
                self.trains[hc] = train

            if action == "clear":
                # Westbound arrival at Angmering P2 = past the crossing
                if train.phase != TrainPhase.CLEARED:
                    logger.info(f"{hc} ({direction.value}): "
                                f"{train.phase.value} → cleared (TRUST arrival at {station})")
                    train.phase = TrainPhase.CLEARED
                    train.confidence = 0.95
                    train.direction = direction
                    train.sub_position = None
                return

            if action == "at_station":
                # TRUST at_station is now just identity + direction enrichment
                # RTT handles the actual station display via sub_position
                train.direction = direction
                train.station = station
                return

            if action == "predict":
                train.direction = direction
                # Estimate crossing time from TRUST offset
                offset = tp.get("offset_secs", 120)
                from datetime import timedelta
                train.predicted_at_crossing = actual_time + timedelta(seconds=offset)

    def handle_rtt_update(self, headcode: str, station: str, platform: str,
                          status: str, origin_codes: list = None, dest_codes: list = None):
        """Process an RTT station status update. Updates sub_position for station berths."""
        with self._lock:
            train = self.trains.get(headcode)
            if not train:
                return  # RTT only enriches, doesn't create

            # Don't update trains that are already cleared or lost
            if train.phase in (TrainPhase.CLEARED, TrainPhase.LOST):
                return

            # Don't update stale trains (avoid matching wrong headcode reuse)
            if train.age_secs > 600:
                return

            now = datetime.now(timezone.utc)

            if status == "AT_PLATFORM":
                # For eastbound (up) trains at Goring: AT_PLATFORM means past the crossing
                # (crossing is BEFORE Goring station for up direction)
                if ("goring" in station.lower() and train.direction == Direction.UP
                        and platform in ("1", None)):
                    if train.phase != TrainPhase.CLEARED:
                        logger.info(f"{headcode} (up): "
                                    f"{train.phase.value} → cleared (RTT AT_PLATFORM {station} P{platform})")
                        train.phase = TrainPhase.CLEARED
                        train.confidence = 0.95
                        train.station = station
                        train.sub_position = None
                        train.last_update = now
                    return

                # For westbound (down) trains at Angmering: AT_PLATFORM means past the crossing
                if (station == "Angmering" and train.direction == Direction.DOWN
                        and platform in ("2", None)):
                    if train.phase != TrainPhase.CLEARED:
                        logger.info(f"{headcode} (down): "
                                    f"{train.phase.value} → cleared (RTT AT_PLATFORM {station} P{platform})")
                        train.phase = TrainPhase.CLEARED
                        train.confidence = 0.95
                        train.station = station
                        train.sub_position = None
                        train.last_update = now
                    return

                # Set at_platform sub-position if train is in a station berth
                if train.last_berth in self.station_berths:
                    logger.info(f"{headcode}: sub_position entry → at_platform "
                                f"(RTT {status} {station} P{platform})")
                    train.sub_position = "at_platform"
                    train.phase = TrainPhase.AT_STATION
                    train.confidence = 0.8
                    train.station = station
                    train.last_update = now
                else:
                    # Train at a station but not in a known station berth — just mark phase
                    logger.info(f"{headcode}: {train.phase.value} → at_station "
                                f"(RTT {status} {station} P{platform})")
                    train.phase = TrainPhase.AT_STATION
                    train.confidence = 0.8
                    train.station = station
                    train.last_update = now

            elif status in ("DEPARTING", "DEPART_READY", "DEPART_PREPARING"):
                logger.debug(f"{headcode}: RTT {status} at {station} P{platform}")
                train.last_update = now

    def _classify_berth(self, berth: str, preferred_direction: Optional[Direction] = None) -> tuple[Optional[TrainPhase], Optional[Direction]]:
        """Determine what phase and direction a berth represents.
        
        If the train already has a known direction, check that direction first
        so shared berths (e.g. A027 = up/approach AND down/clear) resolve correctly.
        """
        directions = list(Direction)
        if preferred_direction:
            directions = [preferred_direction] + [d for d in directions if d != preferred_direction]

        for direction in directions:
            if berth in self.clear_berths[direction]:
                return TrainPhase.CLEARED, direction
            if berth in self.at_crossing_berths[direction]:
                return TrainPhase.AT_CROSSING, direction
            if berth in self.strike_in_berths[direction]:
                return TrainPhase.STRIKE_IN, direction
            if berth in self.approach_berths[direction]:
                return TrainPhase.APPROACHING, direction
        return None, None

    def _estimate_arrival(self, train: TrackedTrain) -> Optional[datetime]:
        """Estimate when the train will reach the crossing based on current phase + heuristics."""
        timing = self.config.get("timing", {})
        from datetime import timedelta

        if train.phase == TrainPhase.STRIKE_IN:
            # Close — use pre_closure time as rough estimate
            secs = timing.get("pre_closure_secs", 120) * 0.5  # Already past the outer zone
            return datetime.now(timezone.utc) + timedelta(seconds=secs)
        elif train.phase == TrainPhase.APPROACHING:
            secs = timing.get("pre_closure_secs", 120)
            return datetime.now(timezone.utc) + timedelta(seconds=secs)
        return None

    def get_active_trains(self) -> list[TrackedTrain]:
        """Return trains that are relevant to the crossing (not cleared or stale)."""
        with self._lock:
            self._cleanup_stale()
            return [t for t in self.trains.values()
                    if t.phase not in (TrainPhase.CLEARED, TrainPhase.LOST)]

    def _cleanup_stale(self):
        """Mark stale trains as LOST, remove very old ones."""
        now = datetime.now(timezone.utc)
        grace_secs = 60  # Allow trains to arrive up to 60s late
        for hc, train in list(self.trains.items()):
            if train.is_stale and train.phase != TrainPhase.LOST:
                # Don't mark as lost if the train is still expected at the crossing soon
                if (train.predicted_at_crossing
                        and (train.predicted_at_crossing - now).total_seconds() > -grace_secs):
                    continue
                logger.info(f"Train {hc} marked as LOST (no update for {train.age_secs:.0f}s)")
                train.phase = TrainPhase.LOST
            # Remove trains that cleared or were lost more than 3 minutes ago
            if train.phase in (TrainPhase.CLEARED, TrainPhase.LOST) and train.age_secs > 180:
                del self.trains[hc]
