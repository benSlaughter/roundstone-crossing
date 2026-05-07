"""
Route-enhanced prediction experiment.

Replays captured TD + SF data from signal_data.db and compares two approaches:
  A) Berth-only prediction (current system) — infers barrier state from train phases only
  B) Route-enhanced prediction — adds LA route state as a parallel signal

For each crossing event, measures:
  - When each method first predicted CLOSING (lead time)
  - When each method confirmed CLOSED (accuracy of timing)
  - When each method predicted OPENING (vs actual last-train clearance)
  - Confidence levels at each stage

Usage:
    python experiments/route_prediction_experiment.py                 # experiment DB (signal_data.db)
    python experiments/route_prediction_experiment.py --server        # server DB (crossing.db, ~9 days)
    python experiments/route_prediction_experiment.py --db path/to.db # custom DB
"""

import argparse
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

EXPERIMENT_DB = Path(__file__).parent / "signal_data.db"
SERVER_DB = Path(__file__).parent.parent / "crossing.db"

# ---------------------------------------------------------------------------
# LA route bit map (from SOP decode)
# ---------------------------------------------------------------------------
# Routes near the crossing (within ~4 berths either side)
# Key = (address, bit), value = (route_name, side)
# side: "east" = routes for signals east of crossing (0032-0035)
#        "west" = routes for signals west of crossing (A007-A010)
CROSSING_ROUTES = {
    ("04", 6): ("R35", "east"),
    ("04", 4): ("R34", "east"),
    ("04", 5): ("R34b", "east"),
    ("04", 3): ("R33", "east"),
    ("04", 2): ("R32", "east"),
    ("03", 7): ("R31", "east"),
    ("04", 0): ("R31b", "east"),
    ("03", 0): ("R29", "east"),
    ("02", 7): ("R28", "east"),
    ("02", 6): ("R27", "east"),
    ("05", 1): ("RA007", "west"),
    ("05", 2): ("RA008", "west"),
    ("03", 4): ("RA010", "west"),
    ("03", 5): ("RA010b", "west"),
}

# Berth zone config (from config.yaml)
APPROACH_BERTHS = {
    "up": {"A027"},
    "down": {"0033"},
}
STRIKE_IN_BERTHS = {
    "up": {"0040", "0038"},
    "down": {"0035", "0037", "0039"},
}
AT_CROSSING_BERTHS = {
    "up": {"0036"},
    "down": {"0041"},
}
CLEAR_BERTHS = {
    "up": {"0034"},
    "down": {"A027"},
}

ALL_RELEVANT = set()
for group in (APPROACH_BERTHS, STRIKE_IN_BERTHS, AT_CROSSING_BERTHS, CLEAR_BERTHS):
    for berths in group.values():
        ALL_RELEVANT |= berths

# Timing constants (from config.yaml)
PRE_CLOSURE_SECS = 120
CROSSING_CLEARANCE_SECS = 10
POST_CLEARANCE_SECS = 5


class Phase(str, Enum):
    APPROACHING = "approaching"
    STRIKE_IN = "strike_in"
    AT_CROSSING = "at_crossing"
    CLEARED = "cleared"
    LOST = "lost"


class BarrierState(str, Enum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    OPENING = "opening"


@dataclass
class SimTrain:
    headcode: str
    direction: str | None = None  # "up" or "down"
    phase: Phase = Phase.APPROACHING
    last_berth: str | None = None
    last_time: datetime | None = None
    first_seen: datetime | None = None


@dataclass
class PredictionEvent:
    """A moment when the predictor changed state."""
    time: datetime
    state: BarrierState
    confidence: float
    trigger: str  # what caused this prediction


@dataclass
class CrossingWindow:
    """A real crossing event derived from berth data."""
    headcode: str
    direction: str
    at_crossing_time: datetime
    cleared_time: datetime | None = None
    approach_time: datetime | None = None


# ---------------------------------------------------------------------------
# Berth-only predictor (Method A — mirrors current inferrer logic)
# ---------------------------------------------------------------------------
class BerthPredictor:
    """Predicts barrier state from train berth positions only."""

    def __init__(self):
        self.state = BarrierState.OPEN
        self.confidence = 0.8
        self.events: list[PredictionEvent] = []
        self.trains: dict[str, SimTrain] = {}
        self._was_closed = False

    def tick(self, now: datetime):
        phases = [t.phase for t in self.trains.values()
                  if t.phase not in (Phase.CLEARED, Phase.LOST)]

        if not phases:
            if self._was_closed:
                self._transition(now, BarrierState.OPENING, 0.8, "all trains cleared")
                self._was_closed = False
            elif self.state == BarrierState.OPENING:
                self._transition(now, BarrierState.OPEN, 0.8, "post-clearance")
            return

        if Phase.AT_CROSSING in phases:
            self._transition(now, BarrierState.CLOSED, 0.9, "train at crossing")
            self._was_closed = True
        elif self._was_closed:
            self._transition(now, BarrierState.CLOSED, 0.85, "trains still active")
        elif Phase.STRIKE_IN in phases:
            self._transition(now, BarrierState.CLOSING, 0.8, "train in strike-in")
        elif Phase.APPROACHING in phases:
            self._transition(now, BarrierState.CLOSING, 0.6, "train approaching")

    def _transition(self, now, state, conf, trigger):
        if state != self.state or conf != self.confidence:
            self.state = state
            self.confidence = conf
            self.events.append(PredictionEvent(now, state, conf, trigger))


# ---------------------------------------------------------------------------
# Route-enhanced predictor (Method B)
# ---------------------------------------------------------------------------
class RouteEnhancedPredictor:
    """Predicts barrier state from train berths + LA route data."""

    def __init__(self):
        self.state = BarrierState.OPEN
        self.confidence = 0.8
        self.events: list[PredictionEvent] = []
        self.trains: dict[str, SimTrain] = {}
        self._was_closed = False
        self.route_state: dict[str, int] = {}  # route_name -> 0/1
        self._route_set_time: dict[str, datetime] = {}

    def update_route(self, now: datetime, route_name: str, value: int):
        old = self.route_state.get(route_name)
        self.route_state[route_name] = value
        if value == 1 and old != 1:
            self._route_set_time[route_name] = now

    def _any_crossing_route_set(self) -> list[str]:
        return [r for r, v in self.route_state.items() if v == 1]

    def tick(self, now: datetime):
        phases = [t.phase for t in self.trains.values()
                  if t.phase not in (Phase.CLEARED, Phase.LOST)]
        active_routes = self._any_crossing_route_set()

        if not phases and not active_routes:
            if self._was_closed:
                self._transition(now, BarrierState.OPENING, 0.85, "all trains cleared + no routes")
                self._was_closed = False
            elif self.state == BarrierState.OPENING:
                self._transition(now, BarrierState.OPEN, 0.85, "post-clearance")
            return

        if Phase.AT_CROSSING in phases:
            self._transition(now, BarrierState.CLOSED, 0.95, "train at crossing + routes")
            self._was_closed = True

        elif self._was_closed:
            # Stay closed while trains OR routes active
            if phases or active_routes:
                self._transition(now, BarrierState.CLOSED, 0.9,
                                 f"trains/routes still active ({len(active_routes)} routes)")
            else:
                self._transition(now, BarrierState.OPENING, 0.85, "all clear")
                self._was_closed = False

        elif Phase.STRIKE_IN in phases and active_routes:
            # Strike-in AND route set = very high confidence barriers are down
            self._transition(now, BarrierState.CLOSED, 0.9,
                             f"strike-in + route SET ({active_routes[0]})")
            self._was_closed = True

        elif active_routes and phases:
            # Route set with any approaching train — barriers likely lowering
            self._transition(now, BarrierState.CLOSING, 0.85,
                             f"route SET ({active_routes[0]}) + train active")

        elif Phase.STRIKE_IN in phases:
            self._transition(now, BarrierState.CLOSING, 0.8, "train in strike-in (no route)")

        elif active_routes:
            # Route set but no train in our zone yet — early warning
            self._transition(now, BarrierState.CLOSING, 0.7,
                             f"route SET ({active_routes[0]}) — early warning")

        elif Phase.APPROACHING in phases:
            self._transition(now, BarrierState.CLOSING, 0.6, "train approaching")

    def _transition(self, now, state, conf, trigger):
        if state != self.state or conf != self.confidence:
            self.state = state
            self.confidence = conf
            self.events.append(PredictionEvent(now, state, conf, trigger))


# ---------------------------------------------------------------------------
# Replay engine
# ---------------------------------------------------------------------------
def classify_berth(to_berth: str) -> tuple[Phase | None, str | None]:
    for direction in ("up", "down"):
        if to_berth in AT_CROSSING_BERTHS[direction]:
            return Phase.AT_CROSSING, direction
        if to_berth in STRIKE_IN_BERTHS[direction]:
            return Phase.STRIKE_IN, direction
        if to_berth in APPROACH_BERTHS[direction]:
            return Phase.APPROACHING, direction
        if to_berth in CLEAR_BERTHS[direction]:
            return Phase.CLEARED, direction
    return None, None


def parse_ts(ts_str: str) -> datetime:
    from dateutil.parser import parse
    return parse(ts_str)


def run_experiment(db_path: Path):
    db = sqlite3.connect(str(db_path))

    # Detect schema: experiment DB has td_events, server DB has train_events
    tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    is_server_db = "train_events" in tables and "td_events" not in tables

    if is_server_db:
        # Server DB: train_events has (headcode, direction, event, from_berth, to_berth, phase, timestamp)
        td_rows = db.execute("""
            SELECT timestamp, from_berth, to_berth, headcode
            FROM train_events WHERE event = 'step'
            ORDER BY timestamp
        """).fetchall()
        # SF events have same schema in both DBs
        sf_rows = db.execute("""
            SELECT timestamp, address, data_hex FROM sf_events
            WHERE area_id = 'LA' ORDER BY timestamp
        """).fetchall()
        print(f"[Using server DB: {db_path.name}]")
    else:
        # Experiment DB: td_events
        raw = db.execute("""
            SELECT timestamp, from_berth, to_berth, headcode
            FROM td_events WHERE area_id = 'LA' AND msg_type = 'CA'
            ORDER BY timestamp
        """).fetchall()
        td_rows = raw
        sf_rows = db.execute("""
            SELECT timestamp, address, data_hex FROM sf_events
            WHERE area_id = 'LA' ORDER BY timestamp
        """).fetchall()
        print(f"[Using experiment DB: {db_path.name}]")

    # Build unified event stream
    events = []
    for row in td_rows:
        ts, fb, tb, hc = row[0], row[1], row[2], row[3]
        events.append(("TD", parse_ts(ts), fb, tb, hc))
    for ts, addr, data_hex in sf_rows:
        events.append(("SF", parse_ts(ts), addr, data_hex, None))
    events.sort(key=lambda e: e[1])

    # Track SF state to detect bit transitions
    sf_state: dict[tuple[str, int], int] = {}

    pred_a = BerthPredictor()
    pred_b = RouteEnhancedPredictor()

    # Build ground truth: actual crossing events from berth data
    crossing_windows: list[CrossingWindow] = []

    for evt in events:
        evt_type = evt[0]
        now = evt[1]

        if evt_type == "TD":
            _, _, from_berth, to_berth, headcode = evt
            if not headcode or not to_berth:
                continue
            if to_berth not in ALL_RELEVANT and from_berth not in ALL_RELEVANT:
                continue

            phase, direction = classify_berth(to_berth)
            if not phase:
                continue

            # Update both predictors' train state
            for pred in (pred_a, pred_b):
                train = pred.trains.get(headcode)
                if not train:
                    train = SimTrain(headcode=headcode, first_seen=now)
                    pred.trains[headcode] = train

                # Don't regress phase
                phase_order = {
                    Phase.APPROACHING: 0, Phase.STRIKE_IN: 1,
                    Phase.AT_CROSSING: 2, Phase.CLEARED: 3,
                }
                if phase_order.get(phase, 0) < phase_order.get(train.phase, 0):
                    continue

                train.phase = phase
                train.direction = direction
                train.last_berth = to_berth
                train.last_time = now

            # Record ground truth
            if phase == Phase.AT_CROSSING:
                crossing_windows.append(CrossingWindow(
                    headcode=headcode, direction=direction or "?",
                    at_crossing_time=now,
                ))
            elif phase == Phase.CLEARED:
                # Find matching crossing window
                for w in reversed(crossing_windows):
                    if w.headcode == headcode and w.cleared_time is None:
                        w.cleared_time = now
                        break
            elif phase == Phase.APPROACHING:
                for w in reversed(crossing_windows):
                    if w.headcode == headcode and w.approach_time is None:
                        w.approach_time = now
                        break

            pred_a.tick(now)
            pred_b.tick(now)

        elif evt_type == "SF":
            _, _, address, data_hex, _ = evt
            val = int(data_hex, 16)
            for bit in range(8):
                bv = (val >> bit) & 1
                key = (address, bit)
                if key in CROSSING_ROUTES:
                    route_name, side = CROSSING_ROUTES[key]
                    if sf_state.get(key) != bv:
                        sf_state[key] = bv
                        pred_b.update_route(now, route_name, bv)

            pred_b.tick(now)

        # Expire stale trains (>5 min no update)
        for pred in (pred_a, pred_b):
            stale = [hc for hc, t in pred.trains.items()
                     if t.last_time and (now - t.last_time).total_seconds() > 300
                     and t.phase not in (Phase.CLEARED, Phase.LOST)]
            for hc in stale:
                pred.trains[hc].phase = Phase.LOST
            # Clean up old cleared/lost trains
            to_remove = [hc for hc, t in pred.trains.items()
                         if t.phase in (Phase.CLEARED, Phase.LOST)
                         and t.last_time and (now - t.last_time).total_seconds() > 600]
            for hc in to_remove:
                del pred.trains[hc]

    # -----------------------------------------------------------------------
    # Analysis
    # -----------------------------------------------------------------------
    print("=" * 75)
    print("ROUTE-ENHANCED PREDICTION EXPERIMENT")
    print("=" * 75)
    print(f"Data: {events[0][1].strftime('%Y-%m-%d %H:%M')} → {events[-1][1].strftime('%H:%M')}")
    print(f"Events: {len(td_rows)} TD + {len(sf_rows)} SF = {len(events)} total")
    print(f"Crossing events (ground truth): {len(crossing_windows)}")
    print()

    # Compare predictions for each crossing event
    print("-" * 75)
    print(f"{'Train':<6} {'Dir':>3} {'At Cross':>9} "
          f"{'A: 1st warn':>12} {'A: lead':>8} {'A: conf':>7} "
          f"{'B: 1st warn':>12} {'B: lead':>8} {'B: conf':>7}")
    print("-" * 75)

    a_leads = []
    b_leads = []
    a_confs = []
    b_confs = []
    b_earlier = 0
    a_earlier = 0
    same_time = 0

    for cw in crossing_windows:
        cross_t = cw.at_crossing_time

        # Find earliest CLOSING/CLOSED prediction before this crossing
        # Look backwards up to 10 min before
        window_start = cross_t - timedelta(minutes=10)

        a_first = None
        a_conf_at_cross = 0.0
        for e in pred_a.events:
            if window_start <= e.time <= cross_t:
                if e.state in (BarrierState.CLOSING, BarrierState.CLOSED) and a_first is None:
                    a_first = e
            if e.time <= cross_t:
                if e.state in (BarrierState.CLOSING, BarrierState.CLOSED):
                    a_conf_at_cross = e.confidence

        b_first = None
        b_conf_at_cross = 0.0
        for e in pred_b.events:
            if window_start <= e.time <= cross_t:
                if e.state in (BarrierState.CLOSING, BarrierState.CLOSED) and b_first is None:
                    b_first = e
            if e.time <= cross_t:
                if e.state in (BarrierState.CLOSING, BarrierState.CLOSED):
                    b_conf_at_cross = e.confidence

        a_lead = (cross_t - a_first.time).total_seconds() if a_first else 0
        b_lead = (cross_t - b_first.time).total_seconds() if b_first else 0

        a_leads.append(a_lead)
        b_leads.append(b_lead)
        a_confs.append(a_conf_at_cross)
        b_confs.append(b_conf_at_cross)

        if b_lead > a_lead + 5:
            b_earlier += 1
        elif a_lead > b_lead + 5:
            a_earlier += 1
        else:
            same_time += 1

        d = "E" if cw.direction == "up" else "W"
        a_t = a_first.time.strftime("%H:%M:%S") if a_first else "   none"
        b_t = b_first.time.strftime("%H:%M:%S") if b_first else "   none"
        marker = " ◀" if b_lead > a_lead + 5 else ""

        print(f"{cw.headcode:<6} {d:>3} {cross_t.strftime('%H:%M:%S'):>9} "
              f"{a_t:>12} {a_lead:>6.0f}s {a_conf_at_cross:>6.0%} "
              f"{b_t:>12} {b_lead:>6.0f}s {b_conf_at_cross:>6.0%}{marker}")

    print("-" * 75)
    print()

    # Summary statistics
    import statistics
    print("=" * 75)
    print("SUMMARY")
    print("=" * 75)

    def stats(vals):
        if not vals:
            return 0, 0, 0
        return statistics.median(vals), min(vals), max(vals)

    a_med, a_min, a_max = stats(a_leads)
    b_med, b_min, b_max = stats(b_leads)

    print(f"                         {'Berth-only (A)':>18}  {'Route-enhanced (B)':>18}")
    print(f"  Warning lead time:")
    print(f"    Median               {a_med:>15.0f}s  {b_med:>15.0f}s")
    print(f"    Range                {a_min:>6.0f}–{a_max:<6.0f}s    {b_min:>6.0f}–{b_max:<6.0f}s")
    print(f"  Confidence at crossing:")
    print(f"    Median               {statistics.median(a_confs):>15.0%}  {statistics.median(b_confs):>15.0%}")
    print()
    print(f"  Route-enhanced warned earlier: {b_earlier}/{len(crossing_windows)} crossings")
    print(f"  Berth-only warned earlier:     {a_earlier}/{len(crossing_windows)} crossings")
    print(f"  Same timing (±5s):             {same_time}/{len(crossing_windows)} crossings")
    print()

    # Breakdown of route-enhanced triggers
    print("--- Route-enhanced predictor trigger breakdown ---")
    trigger_counts = defaultdict(int)
    for e in pred_b.events:
        if e.state in (BarrierState.CLOSING, BarrierState.CLOSED):
            trigger_counts[e.trigger] += 1
    for trigger, count in sorted(trigger_counts.items(), key=lambda x: -x[1]):
        print(f"  {count:>4}x  {trigger}")
    print()

    # False-positive analysis: did either predictor go CLOSING without a crossing?
    print("--- False positive analysis ---")
    for label, pred in [("A (berth-only)", pred_a), ("B (route-enhanced)", pred_b)]:
        closing_events = [e for e in pred.events
                          if e.state in (BarrierState.CLOSING, BarrierState.CLOSED)]
        false_positives = 0
        for ce in closing_events:
            # Check if any crossing happened within ±5 minutes
            matched = any(
                abs((cw.at_crossing_time - ce.time).total_seconds()) < 300
                for cw in crossing_windows
            )
            if not matched:
                false_positives += 1
        print(f"  {label}: {len(closing_events)} warnings, "
              f"{false_positives} false positives "
              f"({false_positives*100//max(len(closing_events),1)}%)")

    # Route-only early warnings (B predicted before A)
    print()
    print("--- Cases where route data provided earlier warning ---")
    for i, cw in enumerate(crossing_windows):
        diff = b_leads[i] - a_leads[i]
        if diff > 5:
            print(f"  {cw.headcode} @ {cw.at_crossing_time.strftime('%H:%M:%S')}: "
                  f"route gave {diff:.0f}s extra lead time "
                  f"(B={b_leads[i]:.0f}s vs A={a_leads[i]:.0f}s)")

    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Route-enhanced prediction experiment")
    parser.add_argument("--server", action="store_true", help="Use server crossing.db (~9 days of data)")
    parser.add_argument("--db", type=str, help="Path to custom SQLite database")
    args = parser.parse_args()

    if args.db:
        db_path = Path(args.db)
    elif args.server:
        db_path = SERVER_DB
    else:
        db_path = EXPERIMENT_DB

    if not db_path.exists():
        print(f"Error: database not found at {db_path}")
        raise SystemExit(1)

    run_experiment(db_path)
