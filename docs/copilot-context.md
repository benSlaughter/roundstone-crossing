# Copilot Context — Roundstone Crossing Predictor

Read this file first to understand the project, its current state, and what needs doing.

## What Is This?

A Python project that **predicts when the barriers at Roundstone Level Crossing (Angmering, West Sussex) will be open or closed**, using live Network Rail train data. There is NO public API for crossing barrier state — we infer it from train positions.

The project also includes an **ESP32-C3 physical barrier logger** for ground-truth data collection.

## The Crossing

- **Name**: Roundstone Level Crossing (Network Rail ID: 1958)
- **Location**: B2140 Roundstone Lane, East Preston/Angmering (50.8165°N, 0.4760°W)
- **Type**: MCB-CCTV — Manually Controlled Barriers with CCTV, 4 full barriers
- **Railway**: West Coastway Line (ELR: BLI1), 70 mph line speed
- **Between**: Angmering (ANG, STANOX 87998) ↔ Goring-by-Sea (GBS, STANOX 87997)
- **Traffic**: ~176 trains/day (Southern passenger + freight)
- **Known issue**: 5+ minute closures common, local complaints about excessive down-time

### Calibrated Barrier Timing (from real observations)
- **Pre-closure**: ~120s before train arrives → barriers lower (confirmed from observations)
- **Crossing clearance**: ~10s for a train to physically clear (at 70mph, 200m ≈ 6s + margin)
- **Post-clearance**: ~5s after last train clears → barriers raise
- **Total last-train-to-opening**: consistently 14-15s across multiple observation windows
- **Note**: Manual observations are ±3-5s per event; derived intervals ±5-10s. Calibration uses median values, not outliers.

## Architecture

```
NROD STOMP Feeds (TD + TRUST + SF)
        │
        ▼
   src/feed.py ──→ src/tracker.py ──→ src/inferrer.py ──→ src/api.py
   (STOMP conn)    (per-train objects) (state inference)    (FastAPI)
                        ↑                      │
                   src/rtt.py                  ▼
                   (RTT polling)         src/history.py
                                        (SQLite logger)
```

### Data Flow
1. **feed.py** connects to NROD via STOMP, receives TD (Train Describer), TRUST (train movement), and S-Class signalling (SF/SG/SH/CT) messages. Resets `last_message_time` on reconnection to prevent stale data flickers.
2. **tracker.py** maintains per-train objects — headcode, direction, phase (approaching/strike_in/at_station/at_crossing/cleared/lost), confidence. Includes stale train cleanup with grace periods (60s past prediction, 5-minute absolute cap).
3. **rtt.py** polls Realtime Trains API for platform-level status at Angmering and Goring-by-Sea, enriching tracked trains with station confirmation and clearing eastbound trains at Goring / westbound at Angmering.
4. **inferrer.py** derives crossing state from the set of active trains — OPEN, CLOSING_PREDICTED, CLOSED_INFERRED, OPENING_PREDICTED, STALE_DATA, UNKNOWN. Key rules:
   - Once CLOSED_INFERRED, barriers stay closed while ANY active trains remain (no state bouncing)
   - OPENING_PREDICTED only shows after actual closure (not after CLOSING_PREDICTED clears)
   - Multi-train closure window merging for accurate opening predictions
5. **history.py** logs state changes, train passages, train events, and SF signalling events to SQLite
6. **api.py** exposes endpoints via FastAPI, serves the web dashboard from `static/`

### Key Design Decisions
- **Train-object-based**: Each train is tracked independently. Crossing state is derived from the SET of active trains (handles multiple simultaneous trains)
- **Confidence-based**: Never claims to know barrier position — outputs are "inferred" with a confidence score
- **No state bouncing**: Once barriers are inferred closed, they stay closed until all trains clear. The only valid exit from CLOSED is → OPENING_PREDICTED → OPEN
- **Configurable berth zones**: `config.yaml` defines which TD berths map to which crossing phase per direction
- **Grace periods**: Trains between berths can go 2+ minutes without updates — they're kept active if their predicted arrival hasn't passed by more than 60s, with a 5-minute absolute cap
- **STALE state**: If feeds drop, crossing state goes to STALE_DATA (never assumes OPEN). Feed reconnection resets the stale timer to prevent brief flickers.

## Current State

### ✅ What's Done
- Full prediction pipeline: feed → tracker → inferrer → API → web dashboard
- 232 automated tests, all passing (~2s)
- S-Class signalling message logging (SF/SG/SH/CT) for future barrier state correlation
- RTT integration for station-level train enrichment (with tests)
- Web dashboard with CSS/JS extracted to separate files (`static/style.css`, `static/app.js`)
- Manual observation data collection (4 days: Apr 28-30, May 1) with precision tracking
- Timing parameters calibrated from real observations
- State machine hardened against bouncing, false opens, stale train artifacts
- ESP32-C3 barrier logger: firmware, documentation, schematics, BOM (~£21)
- GitHub repo: public at `benSlaughter/roundstone-crossing`
- Security audited, `.gitignore` cleaned, `.env.example` with placeholders
- Predictions tab — upcoming crossing closure windows derived from RTT station data, with proximity-coloured cards and auto-refresh
- Docker deployment — multi-stage Dockerfile, docker-compose.yml, CI/CD via GitHub Actions (build + push to GHCR)
- Production deployment — running on server at `crossing.benslaughter.com` with nginx reverse proxy + SSL
- Feedback form — modal in site footer, stored to SQLite, admin-protected GET endpoint (Bearer token via ADMIN_TOKEN env var)
- UTC/BST timezone handling — RTT times correctly tagged as Europe/London before UTC conversion
- Quality audit completed — blocking bugs fixed (thread safety, SQLite timeout), window merging refactored into `src/utils.py`
- Strict CSP headers — all inline styles moved to CSS classes, Content-Security-Policy middleware on all responses
- `/up` health endpoint for uptime monitors (returns 200 + JSON uptime)
- Config validation at startup — `config.yaml` validated with clear error messages on missing/invalid fields

### 🔲 Remaining Work
1. **SF correlation** (blocked) — Attempted to identify which SF address+bit = barrier state. Area LA data (8 addresses) does not correlate with observed closures. The barrier control may be on a different signalling area or not published via NROD. Needs further research or broader SF capture.
2. **ESP32 device build** — Firmware and docs ready, need to order parts (~£21 BOM) and assemble
3. **Schedule context** — No CIF schedule integration for advance prediction
4. **Home Assistant integration** — MQTT notifications for barrier state changes
5. **Observation upload endpoint** — API to accept CSVs from device/phone for automated comparison

## Key Files

| File | Purpose |
|---|---|
| `src/models.py` | CrossingState, TrackedTrain, CrossingStatus dataclasses |
| `src/tracker.py` | Per-train tracking from TD + TRUST + RTT, stale cleanup |
| `src/inferrer.py` | Derives crossing state, no-bounce logic |
| `src/history.py` | SQLite logger (state_intervals, train_passages, train_events, sf_events, feedback) |
| `src/feed.py` | NROD STOMP connection, TD/TRUST/SF message handling, auto-reconnect |
| `src/rtt.py` | Realtime Trains API client for station platform status |
| `src/api.py` | FastAPI endpoints + static file serving |
| `src/main.py` | Entry point, config loading, main loop (2s tick) |
| `config.yaml` | Berth zones, calibrated timing, railway context, station berths |
| `static/index.html` | Web dashboard HTML |
| `static/style.css` | Dashboard styles |
| `static/app.js` | Dashboard JavaScript |
| `src/utils.py` | Shared utilities (window merging, helpers) |
| `tests/` | 232 tests across inferrer, tracker, feed, API, history, RTT, models |
| `device/` | ESP32-C3 barrier logger (firmware, docs, schematics) |
| `data/observations/` | Manual crossing observations with accuracy notes |
| `docs/research.md` | Full research on data sources, APIs, crossing details |
| `Dockerfile` | Multi-stage Python 3.12 build (test → production) |
| `docker-compose.yml` | Container config with persistent volumes, env_file |
| `.github/workflows/build.yml` | CI: tests on PR, Docker build+push to GHCR on main |
| `update.sh` | One-command server deploy script |

## How to Run

```bash
cd ~/projects/roundstone-crossing
source .venv/bin/activate
python -m src.main --api --debug   # predictor + API on 127.0.0.1:8590
python -m pytest tests/ -v         # run test suite (232 tests, ~2s)
```

The server writes its PID to `server.pid` (gitignored).

### Environment
- Python 3.12 venv at `.venv/`
- macOS (Darwin) for development
- Production: Docker on Azure server, nginx reverse proxy, SSL via certbot
- Live at: `crossing.benslaughter.com`
- Credentials in `.env` (gitignored): NROD_USERNAME, NROD_PASSWORD, RTT_TOKEN, ADMIN_TOKEN
- Separate NROD accounts for dev and prod (NROD allows only 1 concurrent STOMP connection per account)

## Technical Reference

### NROD Feed Topics
- TD: `/topic/TD_ALL_SIG_AREA` — train positions at berth level
- TRUST: `/topic/TRAIN_MVT_ALL_TOC` — train movements at timing points

### TD Message Types
- `CA_MSG`: berth step (train moved from berth A to berth B)
- `CB_MSG`: berth cancel (train disappeared from berth)
- `CC_MSG`: berth interpose (train appeared in berth, no origin)

### S-Class Message Types
- `SF_MSG`: signalling element state change (area, address, data byte)
- `SG_MSG`: bulk signalling refresh on connection
- `SH_MSG`: refresh complete marker
- `CT_MSG`: heartbeat

### Crossing State Machine
```
UNKNOWN ──→ OPEN ──→ CLOSING_PREDICTED ──→ CLOSED_INFERRED ──→ OPENING_PREDICTED ──→ OPEN
                                                    │                                  │
                                                    └──── stays CLOSED while any ──────┘
                                                          active trains remain
STALE_DATA ←── (any state, if feed age > 300s)
```

### MCB-CCTV Barrier Timing (calibrated)
- Minimum warning time: 27 seconds (GK/RT0192 standard)
- Pre-closure: ~120s (signaller lowers barriers ~2 min before train)
- Crossing clearance: ~10s (train physically clears the crossing zone)
- Post-clearance: ~5s (signaller verifies CCTV and raises barriers)
- Total closure per train: typically 2-3 minutes
- Consecutive trains: signaller keeps barriers down between closely-spaced trains

### Manual Observation Accuracy
- Each button press: ±3-5s (human reaction time + phone delay)
- Derived intervals (e.g. train-to-opening): ±5-10s (two imprecise measurements)
- Calibration uses median values across observations, not outliers
- Device-logged data will be ~100ms accuracy when ESP32 is built
