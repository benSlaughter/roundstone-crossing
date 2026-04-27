# Copilot Context — Roundstone Crossing Predictor

Read this file first to understand the project, its current state, and what needs doing.

## What Is This?

A Python project that **predicts when the barriers at Roundstone Level Crossing (Angmering, West Sussex) will be open or closed**, using live Network Rail train data. There is NO public API for crossing barrier state — we infer it from train positions.

## The Crossing

- **Name**: Roundstone Level Crossing (Network Rail ID: 1958)
- **Location**: B2140 Roundstone Lane, East Preston/Angmering (50.8165°N, 0.4760°W)
- **Type**: MCB-CCTV — Manually Controlled Barriers with CCTV, 4 full barriers
- **Railway**: West Coastway Line (ELR: BLI1), 70 mph line speed
- **Between**: Angmering (ANG, STANOX 87998) ↔ Goring-by-Sea (GOR, STANOX 87997)
- **Traffic**: ~176 trains/day (Southern passenger + freight)
- **Barrier timing**: ~120s before train → barriers lower, ~15s after clear → barriers raise
- **Known issue**: 5+ minute closures common, local complaints about excessive down-time

## Architecture

```
NROD STOMP Feeds (TD + TRUST)
        │
        ▼
   src/feed.py ──→ src/tracker.py ──→ src/inferrer.py ──→ src/api.py
   (STOMP conn)    (per-train objects) (state inference)    (FastAPI)
                                              │
                                              ▼
                                       src/history.py
                                       (SQLite logger)
```

### Data Flow
1. **feed.py** connects to NROD via STOMP, receives TD (Train Describer) and TRUST (train movement) messages
2. **tracker.py** maintains per-train objects — headcode, direction, phase (approaching/strike_in/at_crossing/cleared), confidence
3. **inferrer.py** derives crossing state from the set of active trains — OPEN, CLOSING_PREDICTED, CLOSED_INFERRED, OPENING_PREDICTED, STALE_DATA, UNKNOWN
4. **history.py** logs every state change interval + train passage to SQLite
5. **api.py** exposes /status, /predictions, /history, /stats via FastAPI

### Key Design Decisions
- **Train-object-based**: Each train is tracked independently. Crossing state is derived from the SET of active trains (handles multiple simultaneous trains)
- **Confidence-based**: Never claims to know barrier position — outputs are "inferred" with a confidence score
- **Configurable berth zones**: `config.yaml` defines which TD berths map to which crossing phase (approach/strike_in/at_crossing/clear) per direction
- **Empirical timing**: Starts with heuristics, designed to calibrate from observed data over time
- **STALE state**: If feeds drop, crossing state goes to STALE_DATA (never assumes OPEN)

## Current State

### ✅ What's Done
- All 7 source modules written and importing clean
- Models: CrossingState enum, TrackedTrain dataclass, CrossingStatus
- TrainTracker: handles TD berth steps + TRUST movements, classifies trains by phase
- CrossingInferrer: derives state with confidence, handles multi-train scenarios
- HistoryLogger: SQLite with state_intervals, train_passages, raw_events tables
- NRODFeed: STOMP connection, message parsing, TD/TRUST dispatch
- FastAPI endpoints: /status, /predictions, /history, /stats
- Config: crossing details, railway info, timing heuristics, berth zone structure
- Python 3.12 venv with all deps

### ❌ What's NOT Done (blockers for first run)
1. ~~**No NROD account yet**~~ — ✅ Registered
2. ~~**No berth zones mapped**~~ — ✅ Mapped from SMART data (TD area is **LA**, not ES)
3. ~~**No SMART/CORPUS data downloaded**~~ — ✅ Downloaded to `data/`

### Detailed TODO
See `docs/TODO.md` for the full prioritised task list.

## Key Files

| File | Purpose |
|---|---|
| `src/models.py` | CrossingState, TrackedTrain, CrossingStatus dataclasses |
| `src/tracker.py` | Per-train tracking from TD + TRUST messages |
| `src/inferrer.py` | Derives crossing state from active trains |
| `src/history.py` | SQLite historical logger |
| `src/feed.py` | NROD STOMP connection and message parsing |
| `src/api.py` | FastAPI endpoints |
| `src/main.py` | Entry point, config loading, main loop |
| `config.yaml` | Berth zones, timing heuristics, railway context |
| `docs/research.md` | Full research on data sources, APIs, crossing details |
| `docs/TODO.md` | Prioritised task list |

## How to Run (once NROD account + berths are configured)

```bash
cd ~/projects/roundstone-crossing
source .venv/bin/activate
python -m src.main --debug        # predictor only
python -m src.main --api --debug  # predictor + API on port 8590
```

## Technical Reference

### NROD Feed Topics
- TD: `/topic/TD_ALL_SIG_AREA` — train positions at berth level
- TRUST: `/topic/TRAIN_MVT_ALL_TOC` — train movements at timing points

### TD Message Types
- `CA_MSG`: berth step (train moved from berth A to berth B)
- `CB_MSG`: berth cancel (train disappeared from berth)
- `CC_MSG`: berth interpose (train appeared in berth, no origin)
- `CT_MSG`: heartbeat

### Crossing State Machine
```
UNKNOWN ──→ OPEN ──→ CLOSING_PREDICTED ──→ CLOSED_INFERRED ──→ OPENING_PREDICTED ──→ OPEN
                                                                                       │
STALE_DATA ←── (any state, if feed connection lost for >5 min) ────────────────────────┘
```

### MCB-CCTV Barrier Timing (standards)
- Minimum warning time: 27 seconds (GK/RT0192)
- Typical pre-closure: ~120 seconds before train arrives
- Post-clearance: ~10-20 seconds after train clears
- Total typical closure: 2-3 minutes per train
- Signaller-controlled — actual timing varies with practice

### Owner / Environment
- Developer: Ben (guybrush), macOS, VSCode
- Python 3.12 venv at `.venv/`
- Other projects: `~/projects/home-assistant/`, `~/projects/personal-assistant/`, `~/projects/waitrose/`, `~/projects/sainsburys-cli-mcp/`
- Home Assistant running on HA Green (homeassistant.local)
- Will eventually integrate with HA via MQTT for notifications
