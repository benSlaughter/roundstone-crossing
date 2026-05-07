"""
Crossing state model — defines the possible inferred states and the per-train tracking objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class CrossingState(str, Enum):
    UNKNOWN = "unknown"
    OPEN = "open"
    CLOSING_PREDICTED = "closing_predicted"
    CLOSED_INFERRED = "closed_inferred"
    OPENING_PREDICTED = "opening_predicted"
    STALE_DATA = "stale_data"


class Direction(str, Enum):
    UP = "up"        # Towards London/Brighton (eastbound)
    DOWN = "down"    # Towards Portsmouth (westbound)


class TrainPhase(str, Enum):
    """Where a train is relative to the crossing."""
    APPROACHING = "approaching"     # In approach zone
    STRIKE_IN = "strike_in"         # In strike-in zone (closure likely imminent)
    AT_CROSSING = "at_crossing"     # At or over the crossing
    CLEARED = "cleared"             # Past the crossing
    AT_STATION = "at_station"       # At Angmering or Goring (confirmed by RTT)
    LOST = "lost"                   # No updates for too long


@dataclass
class TrackedTrain:
    """A train being tracked as it approaches/passes the crossing."""
    headcode: str                          # e.g., "1A23"
    train_id: str | None = None         # TRUST train UID if correlated
    direction: Direction | None = None
    phase: TrainPhase = TrainPhase.APPROACHING
    last_berth: str | None = None
    last_berth_time: datetime | None = None
    first_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    predicted_at_crossing: datetime | None = None
    confidence: float = 0.5                # 0.0 = guess, 1.0 = certain
    station: str | None = None          # Station name if AT_STATION
    sub_position: str | None = None     # "entry" or "at_platform" within station berths
    _passage_logged: bool = False          # Whether this train's passage has been logged

    @property
    def age_secs(self) -> float:
        return (datetime.now(timezone.utc) - self.last_update).total_seconds()

    @property
    def is_stale(self) -> bool:
        return self.age_secs > 120  # 2 minutes without update


@dataclass
class CrossingStatus:
    """The current inferred state of the crossing."""
    state: CrossingState = CrossingState.UNKNOWN
    confidence: float = 0.0
    since: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    predicted_change: datetime | None = None
    predicted_next_state: CrossingState | None = None
    active_trains: list[TrackedTrain] = field(default_factory=list)
    last_feed_message: datetime | None = None
    # Human-readable explanation of WHY the current state was entered.
    # Set by the inferrer at each transition; preserved unchanged when the
    # state is re-asserted on subsequent ticks (so it always describes the
    # original entry condition, not the most recent tick).
    reason: str | None = None

    def seconds_until_change(self) -> float | None:
        if self.predicted_change:
            delta = (self.predicted_change - datetime.now(timezone.utc)).total_seconds()
            return max(0, delta)
        return None

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "since": self.since.isoformat(),
            "seconds_in_state": round((datetime.now(timezone.utc) - self.since).total_seconds()),
            "predicted_change": self.predicted_change.isoformat() if self.predicted_change else None,
            "seconds_until_change": round(self.seconds_until_change()) if self.seconds_until_change() is not None else None,
            "predicted_next_state": self.predicted_next_state.value if self.predicted_next_state else None,
            "active_trains": [
                {
                    "headcode": t.headcode,
                    "direction": t.direction.value if t.direction else None,
                    "phase": t.phase.value,
                    "last_berth": t.last_berth,
                    "station": t.station,
                    "confidence": round(t.confidence, 2),
                }
                for t in self.active_trains
            ],
        }
