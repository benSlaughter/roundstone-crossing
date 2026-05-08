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
- **Pre-closure**: route SET → at-crossing median 212s (10-day production data). Per MCB-CCTV procedure, barriers are confirmed down ≥ this lead time. Inferrer uses `pre_closure_secs: 180` for the train-only fallback path (compromise between P25 150s and median 212s). Old 120s figure was based on a small observation sample and underestimated.
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
- 342 automated tests, all passing (~4s)
- S-Class signalling message logging (SF/SG/SH/CT) for future barrier state correlation
- RTT integration for station-level train enrichment (with tests)
- Web dashboard with CSS/JS extracted to separate files (`static/style.css`, `static/app.js`)
- Manual observation data collection (4 days: Apr 28-30, May 1) with precision tracking
- Timing parameters calibrated from real production data (10 days)
- State machine hardened against bouncing, false opens, stale train artifacts
- ESP32-C3 barrier logger: firmware, documentation, schematics, BOM (~£21)
- GitHub repo: public at `benSlaughter/roundstone-crossing`
- Security audited, `.gitignore` cleaned, `.env.example` with placeholders
- Predictions tab — upcoming crossing closure windows derived from RTT station data, with proximity-coloured cards and auto-refresh
- Docker deployment — multi-stage Dockerfile, docker-compose.yml, CI/CD via GitHub Actions (build + push to GHCR)
- Production deployment — running on server at `crossing.benslaughter.com` with nginx reverse proxy + SSL
- Feedback form — modal in site footer, stored to SQLite, admin-protected GET endpoint (Bearer token via ADMIN_TOKEN env var, constant-time comparison via `hmac.compare_digest`)
- `/admin/db.sqlite.gz` — admin-protected gzipped SQLite snapshot endpoint for self-service data pulls. Uses SQLite online backup API (WAL-safe, doesn't lock the live DB). Accepts token via `Authorization: Bearer` header OR `?token=` query string. Typical compressed size: 30-40% of original (2-5 MB per week of data).
- `/live` and `/live/data` are admin-gated when `ADMIN_TOKEN` is set (open in dev for ergonomics). Bookmark as `/live?token=<your-admin-token>` for browser use.
- UTC/BST timezone handling — RTT times correctly tagged as Europe/London before UTC conversion
- Quality audit completed (May 2026 + Aug 2026) — blocking bugs fixed, window merging in `src/utils.py`, full security-header set
- Strict security headers — Content-Security-Policy, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy on every response
- `/up` health endpoint for uptime monitors (returns 200 + JSON uptime)
- Config validation at startup — `config.yaml` validated with clear error messages on missing/invalid fields
- LA SOP route monitoring — 14 crossing-area route bits (R27-R35, RA007-RA010) tracked via `src/route_monitor.py`, shown live on `/live`, every transition logged to `sf_events`
- Route-hold cap — stuck routes downgrade to UNKNOWN after `max_route_hold_secs` (15 min default) instead of locking us in CLOSED indefinitely
- OPENING via route-clear — when all routes clear with no train, briefly emit `OPENING_PREDICTED` (signaller verifies CCTV before raising)
- State `reason` field — every state transition records WHY it was entered (e.g. `"train at crossing: 1H42 + routes (R32)"`); shown on `/live`, persisted to `state_intervals.reason`
- Berth/direction correction — A027 is DOWN-only (clear berth); 0042 is the UP-approach berth. LA convention: even=UP (eastbound), odd=DOWN (westbound)
- Geography correction — Roundstone is WEST of Goring, EAST of Angmering (~885m east of Angmering platform)

### 🔲 Remaining Work
1. **Route-based inference is currently DISABLED** — `inference.use_routes: false` in `config.yaml` after a 2026-05-08 production regression where route-based inference reported CLOSED while barriers were OPEN. Routes are still monitored, logged, and shown on `/live`. Re-enable after building a proper state-coverage metric (#2 below) and per-route reliability weighting.
2. **State-coverage metric** — Currently we have no way to fairly evaluate predictor changes. Need: "% of time predictor correctly says CLOSED during actual closures" + "% of asserted-CLOSED time that matched a real closure". Required before any further inference tuning.
3. **ESP32 device build** — Firmware and docs ready, need to order parts (~£21 BOM) and assemble
4. **Schedule context** — No CIF schedule integration for advance prediction
5. **Home Assistant integration** — MQTT notifications for barrier state changes
6. **Observation upload endpoint** — API to accept CSVs from device/phone for automated comparison

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
| `tests/` | 342 tests across inferrer, tracker, feed, API, history, RTT, models, route_monitor, security headers |
| `device/` | ESP32-C3 barrier logger (firmware, docs, schematics) |
| `data/observations/` | Manual crossing observations with accuracy notes |
| `docs/research.md` | Full research on data sources, APIs, crossing details |
| `Dockerfile` | Multi-stage Python 3.12 build (test → production) |
| `docker-compose.yml` | Container config with persistent volumes, env_file |
| `.github/workflows/build.yml` | CI: tests on PR, Docker build+push to GHCR on main |
| `update.sh` | One-command server deploy script |

## Directory Layout

```
roundstone-crossing/
├── src/                              # Application code
│   ├── api.py                        # FastAPI endpoints (/status, /predictions, /live, etc.)
│   ├── feed.py                       # NROD STOMP connection (TD/TRUST/SF)
│   ├── history.py                    # SQLite history logger
│   ├── inferrer.py                   # Crossing state machine
│   ├── main.py                       # Entry point
│   ├── models.py                     # Dataclasses (TrackedTrain, CrossingStatus, etc.)
│   ├── route_monitor.py              # SF route SET/CLEAR tracker for LA area
│   ├── rtt.py                        # Realtime Trains API client
│   ├── tracker.py                    # Per-train tracking from TD/TRUST/RTT
│   └── utils.py                      # Shared helpers
├── static/                           # Web dashboard assets
│   ├── index.html                    # Main public dashboard
│   ├── app.js                        # Main dashboard JS
│   ├── style.css                     # Main dashboard CSS
│   ├── live.html                     # Hidden /live debug view (raw data)
│   ├── live.js                       # Live view JS
│   └── live.css                      # Live view CSS
├── tests/                            # Pytest suite (342 tests)
│   ├── conftest.py                   # Shared fixtures
│   ├── test_api.py                   # API endpoint tests
│   ├── test_feed.py                  # NROD feed/route monitor integration
│   ├── test_history.py               # SQLite logger
│   ├── test_inferrer.py              # State machine + route-enhanced
│   ├── test_models.py                # Dataclass behaviour
│   ├── test_route_monitor.py         # SF parsing, golden bytes
│   ├── test_rtt.py                   # RTT API client
│   └── test_tracker.py               # Train tracking + classification
├── docs/                             # Project documentation
│   ├── copilot-context.md            # THIS FILE — quick context for AI/devs
│   ├── research.md                   # Full research on data sources, APIs, crossing
│   ├── roadmap.md                    # Long-term roadmap
│   ├── TODO.md                       # Active task list
│   ├── quality-audit.md              # Code quality audit notes
│   ├── nrod-datasheet/               # Comprehensive NROD reference
│   │   ├── README.md
│   │   ├── 01-connection.md
│   │   ├── 02-td-messages.md
│   │   ├── 03-sf-messages.md
│   │   ├── 04-area-mapping.md
│   │   ├── 05-observations.md
│   │   ├── 06-la-sop.md              # LA (Lancing) Standard Operating Procedure
│   │   └── 07-bm-sop.md              # BM (Barnham) Standard Operating Procedure
│   └── wiki-pages/                   # Saved Open Rail Data wiki HTML (gitignored)
│       ├── BM - Open Rail Data Wiki.html
│       ├── C Class Messages - Open Rail Data Wiki.html
│       ├── Decoding S-Class Data - Open Rail Data Wiki.html
│       ├── LA - Open Rail Data Wiki.html
│       ├── List of Train Describers - Open Rail Data Wiki.html
│       ├── RTPPM - Open Rail Data Wiki.html
│       ├── Reference Data - Open Rail Data Wiki.html
│       └── S Class Messages - Open Rail Data Wiki.html
├── experiments/                      # One-off analysis scripts (not in production loop)
│   ├── README.md                     # Explains what experiments are for
│   ├── analyse_signals.py            # SF signal correlation analysis
│   ├── route_prediction_experiment.py # Route-enhanced prediction validation
│   ├── raw_dumper.py                 # Captures raw NROD messages for analysis
│   ├── signal_logger.py              # Long-running SF logger (PID in logger.pid)
│   ├── signal_data.db                # SF event capture DB (gitignored)
│   ├── signal_log.jsonl              # Append-only SF log (gitignored)
│   ├── images/                       # Reference signal diagrams (gitignored)
│   │   ├── angmering.png             # OpenTrainTimes signalling diagram
│   │   ├── angmering-vaildata.png    # Sectional Appendix Table A (Angmering)
│   │   ├── goring-vaildata.png       # Sectional Appendix Table A (Goring)
│   │   ├── barnham.png               # Barnham OTT diagram
│   │   └── littlehampton.png         # Littlehampton OTT diagram
│   └── raw_dumps/                    # Raw NROD topic captures (gitignored)
│       ├── RTPPM_ALL.jsonl
│       ├── TD_ALL_SIG_AREA.jsonl
│       ├── TRAIN_MVT_ALL_TOC.jsonl
│       ├── TSR_ALL_ROUTE.jsonl
│       └── VSTP_ALL.jsonl
├── data/                             # Reference + observation data
│   ├── corpus.json                   # Network Rail CORPUS (TIPLOC/STANOX)
│   ├── smart.json                    # Network Rail SMART (berth → location)
│   └── observations/                 # Manual crossing observations
│       ├── README.md
│       └── YYYY-MM-DD.csv            # One file per observation day
├── device/                           # ESP32-C3 hardware barrier logger
│   ├── README.md
│   ├── docs/
│   │   ├── assembly.md
│   │   └── schematic.md
│   └── firmware/barrier_logger/      # PlatformIO firmware
├── scripts/                          # Helper scripts
│   ├── download_reference_data.py    # Fetch CORPUS/SMART JSON
│   └── find_berths.py                # Berth lookup utility
├── logs/                             # Runtime logs (gitignored)
├── .github/workflows/build.yml       # CI: tests + Docker build/push to GHCR
├── config.yaml                       # All configuration (berths, timing, routes)
├── crossing.db                       # Live SQLite DB (gitignored)
├── Dockerfile                        # Multi-stage Python 3.12 build
├── docker-compose.yml                # Container with persistent volumes
├── requirements.txt                  # Python deps
├── update.sh                         # One-command deploy script
└── README.md                         # Project overview
```

**Gitignored:** `crossing.db`, `logs/`, `experiments/images/`, `experiments/raw_dumps/`,
`experiments/signal_data.db*`, `experiments/signal_log.jsonl`, `docs/wiki-pages/`,
`.venv/`, `.env`, `server.pid`.

## How to Run

```bash
cd ~/projects/roundstone-crossing
source .venv/bin/activate
python -m src.main --api --debug   # predictor + API on 127.0.0.1:8590
python -m pytest tests/ -v         # run test suite (342 tests, ~4s)
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
- Pre-closure: ~180s configured (route SET ≥ 212s before train, barriers confirmed down at that point per MCB-CCTV procedure)
- Crossing clearance: ~10s (train physically clears the crossing zone)
- Post-clearance: ~5s (signaller verifies CCTV and raises barriers)
- Total closure per train: typically 2-3 minutes
- Consecutive trains: signaller keeps barriers down between closely-spaced trains

### Manual Observation Accuracy
- Each button press: ±3-5s (human reaction time + phone delay)
- Derived intervals (e.g. train-to-opening): ±5-10s (two imprecise measurements)
- Calibration uses median values across observations, not outliers
- Device-logged data will be ~100ms accuracy when ESP32 is built

### Signal Layout (verified from OpenTrainTimes + Sectional Appendix BLI1)

**Mileages (ELR BLI1, from Brighton):**
- Goring-by-Sea station: 13m 07ch
- Goring LC (CCTV): 13m 10ch
- Ferring LC (CCTV): 13m 56ch
- Langmeads No.1 Crossing: 14m 31ch
- Roundstone LC (CCTV): 15m 00ch
- Angmering Substation: 15m 25ch
- Angmering station: 15m 44ch
- Angmering LC (CCTV): 15m 48ch
- Brook Lane Crossing: 16m 45ch
- Norway Lane Crossing: 17m 12ch

**Roundstone is WEST of Goring, EAST of Angmering** (44 chains ≈ 885m east of Angmering station).
Lower BLI1 mileage = closer to Brighton = east. So west→east order is: Angmering (15m 44ch) → Roundstone (15m 00ch) → Goring (13m 07ch).
BPLAN network link distance Angmering→Goring is 3958m; Roundstone sits ~885m east of Angmering platform and ~3073m west of Goring.

**Signal numbering convention (UK):**
- Odd numbers = DOWN line (westbound, towards London/Brighton)
- Even numbers = UP line (eastbound, towards Portsmouth/Littlehampton)

**Signals around Roundstone crossing (from OTT diagram, west→east):**
- UP line (eastbound, top): 8 — 30 — 42 — 40 — [ANG P1] — 38 — |ROUNDSTONE LC| — 36 — 34 — 32
- DOWN line (westbound, bottom): 33 — 31 — 29 — 27 — [ANG P2] — 41 — 39 — |ROUNDSTONE LC| — 37 — 35 — 33

**Signaller control area boundary:**
- AR (Arundel SB) controls west of Angmering area
- LG (Lancing SB) controls east of Angmering area (including Roundstone)
- Boundary line marked between signal 27 and signal 42 area

**Train Describer:** TCB (Track Circuit Block) signalling, RA8 routing, DC: Brighton.

### Route → Signal Mapping (LA TD area)

Route names in NROD SF data correspond to physical signals:
- **R-prefix routes** (R27, R28, R29, R31, R31b, R32, R33, R34, R34b, R35) = routes from numbered signals in the Lancing (LG) area
- **RA-prefix routes** (RA007, RA008, RA010, RA010b) = routes from Arundel (AR) area signals (different numbering scheme)
- The "b" suffix (R31b, R34b, RA010b) indicates a subsidiary/alternate route from the same signal
- "east/west side" labels in config.yaml refer to which side of Roundstone crossing the controlling signal sits, NOT the direction of travel
- A single train movement may set 1-3 routes in sequence (A→B, B→C). Most common pairings: R27+R29 (97x), RA008+RA010 (55x), R31+R33 (49x)

### Routes — Verified Behaviour (from 9 days of data)
- Routes are SET by the signaller and CLEARED by train passage (or 240s cancellation timeout)
- Routes are strictly directional (A→B only, never wrong-way)
- For MCB-CCTV crossings: route SET requires barriers already down + CCTV verification
- 98.8% of crossings have at least one LA route SET within ±180s
- No single route fires for all crossings — most common is R27 at 49% of all closures
- "East side" routes (R-prefix) and "west side" routes (RA-prefix) both fire for both directions of travel — they don't indicate train direction
- Route SET happens 50–100s before barrier-down observation, providing early warning
