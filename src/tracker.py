"""
Train tracker — maintains per-train objects from TD and TRUST messages.
Correlates TD berth steps with TRUST train identities.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from .models import TrackedTrain, TrainPhase, Direction

logger = logging.getLogger("crossing.tracker")


class TrainTracker:
    """Tracks trains approaching/passing the crossing based on TD and TRUST data."""

    def __init__(self, config: dict):
        self.config = config
        self.trains: dict[str, TrackedTrain] = {}  # keyed by headcode
        self._load_berth_zones()

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
            logger.warning("⚠️  No berth zones configured — TD tracking will not work until berths are mapped")

    def handle_td_step(self, from_berth: str, to_berth: str, headcode: str, timestamp: datetime):
        """Process a TD berth step message. Updates the tracked train's position."""
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
            logger.info(f"🚂 New train spotted: {headcode} at berth {to_berth}")

        train.last_berth = to_berth
        train.last_berth_time = now
        train.last_update = now

        # Determine direction and phase from the berth
        new_phase, direction = self._classify_berth(to_berth)
        if direction:
            train.direction = direction
        if new_phase:
            if new_phase != train.phase:
                logger.info(f"🚂 {headcode} ({train.direction.value if train.direction else '?'}): "
                            f"{train.phase.value} → {new_phase.value} (berth {to_berth})")
            train.phase = new_phase

        # Predict when train will be at crossing
        if new_phase in (TrainPhase.APPROACHING, TrainPhase.STRIKE_IN):
            train.predicted_at_crossing = self._estimate_arrival(train)
            train.confidence = 0.7 if new_phase == TrainPhase.STRIKE_IN else 0.5

        elif new_phase == TrainPhase.AT_CROSSING:
            train.predicted_at_crossing = now
            train.confidence = 0.9

        elif new_phase == TrainPhase.CLEARED:
            train.confidence = 0.9

    def handle_trust_movement(self, train_id: str, tiploc: str, event_type: str,
                               actual_time: datetime, headcode: Optional[str] = None):
        """Process a TRUST train movement message. Used for identity and fallback timing."""
        trust_config = self.config.get("trust", {}).get("timing_points", [])

        # Find matching timing point
        matching = [tp for tp in trust_config if tp["tiploc"] == tiploc]
        if not matching:
            return

        tp = matching[0]
        direction = Direction(tp["direction"])

        # Try to correlate with existing TD-tracked train
        if headcode and headcode in self.trains:
            train = self.trains[headcode]
            train.train_id = train_id
            train.direction = direction
            train.last_update = actual_time
        else:
            # Create from TRUST (lower confidence — no berth-level data)
            hc = headcode or train_id[:4]
            train = TrackedTrain(
                headcode=hc,
                train_id=train_id,
                direction=direction,
                phase=TrainPhase.APPROACHING,
                confidence=0.4,
                last_update=actual_time,
            )
            self.trains[hc] = train
            logger.info(f"🚂 Train from TRUST: {hc} ({direction.value}) at {tiploc}")

        # Estimate crossing time from TRUST offset
        offset = tp.get("offset_secs", 120)
        from datetime import timedelta
        train.predicted_at_crossing = actual_time + timedelta(seconds=offset)

    def _classify_berth(self, berth: str) -> tuple[Optional[TrainPhase], Optional[Direction]]:
        """Determine what phase and direction a berth represents."""
        for direction in Direction:
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
        self._cleanup_stale()
        return [t for t in self.trains.values()
                if t.phase not in (TrainPhase.CLEARED, TrainPhase.LOST)]

    def _cleanup_stale(self):
        """Mark stale trains as LOST, remove very old ones."""
        for hc, train in list(self.trains.items()):
            if train.is_stale and train.phase != TrainPhase.LOST:
                logger.info(f"👻 Train {hc} marked as LOST (no update for {train.age_secs:.0f}s)")
                train.phase = TrainPhase.LOST
            # Remove trains that cleared or were lost more than 10 minutes ago
            if train.phase in (TrainPhase.CLEARED, TrainPhase.LOST) and train.age_secs > 600:
                del self.trains[hc]
