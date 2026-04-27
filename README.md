# Roundstone Crossing Predictor 🚂

Predicts when the barriers at Roundstone Level Crossing (Angmering, West Sussex) will be open or closed, using live Network Rail train data.

## How it works

1. **Listens** to Network Rail's real-time STOMP feeds (Train Describer + TRUST)
2. **Tracks** individual trains approaching the crossing from both directions
3. **Infers** crossing barrier state (open/closing/closed/opening) with confidence levels
4. **Learns** actual timings over time to improve prediction accuracy
5. **Logs** every state change to SQLite for historical analysis

## Crossing Details

- **Location**: Roundstone Level Crossing, B2140, East Preston/Angmering
- **Type**: MCB-CCTV (Manually Controlled Barriers with CCTV)
- **Railway**: West Coastway Line (BLI1), 70 mph
- **Between**: Angmering (ANG) ↔ Goring-by-Sea (GOR)
- **Traffic**: ~176 trains/day

## Setup

### Prerequisites
- Python 3.11+
- Network Rail Open Data account ([register here](https://publicdatafeeds.networkrail.co.uk))

### Install
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your NROD credentials
```

### Run
```bash
python -m src.main          # Start the predictor
python -m src.main --api    # Start with API server
```

### API
```
GET /status       — Current crossing state + confidence
GET /predictions  — Upcoming trains + predicted closure windows
GET /history      — Query historical open/close intervals
```

## Architecture

```
NROD STOMP ──→ Train Tracker ──→ Crossing Inferrer ──→ API + Logger
  (TD+TRUST)    (per-train)      (state + confidence)   (FastAPI + SQLite)
```

## States

| State | Meaning |
|---|---|
| `UNKNOWN` | No data / just started |
| `OPEN` | No trains approaching, crossing clear |
| `CLOSING_PREDICTED` | Train detected in approach zone, closure expected in ~Xs |
| `CLOSED_INFERRED` | Train at/near crossing, barriers likely down |
| `OPENING_PREDICTED` | Train passed, barriers likely rising |
| `STALE_DATA` | Feed connection lost, state unreliable |

## Licence

MIT
