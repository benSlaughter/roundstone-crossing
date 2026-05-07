# Roundstone Level Crossing — Research & Investigation

## 🎯 Goal
Build a system to monitor the Roundstone Level Crossing in Angmering, integrate status/predictions into Home Assistant, and get notifications when the crossing is about to close (or is closed).

---

## 📍 Crossing Details

### Location
- **Name**: Roundstone Level Crossing
- **Network Rail ID**: 1958
- **Road**: B2140 Roundstone Lane / Worthing Road, at junction with North Lane
- **Postcode**: BN16 1AG / BN16 1AF
- **Coordinates**: 50.816519°N, 0.476006°W (OS Grid TQ074029)
- **Area**: East Preston / Angmering, Arun District, West Sussex
- **ELR (Engineers Line Reference)**: BLI1 (Brighton to Littlehampton)

### Infrastructure
- **Type**: MCB-CCTV — Manually Controlled Barriers with CCTV monitoring
- **Barriers**: 4 full barriers (both directions)
- **Warning lights**: 6 LED wigwag lights (complex junction layout)
- **Operation**: Barriers lowered/raised by a remote signaller watching CCTV
- **Managed by**: Network Rail

### Railway Line
- **Route**: West Coastway Line (Brighton → Worthing → Littlehampton → Portsmouth)
- **Between stations**: Angmering (ANG) and Goring-by-Sea (GBS)
- **Distance from Brighton**: ~15 miles 44 chains (25.0 km)
- **Operator**: Southern (Govia Thameslink Railway)
- **Platforms at Angmering**: 2

### Train Services
- Southern services between Brighton, Worthing, Littlehampton, and Portsmouth
- **~176 trains per day** past the crossing (passenger + freight)
- Typical frequency: 4-6 trains per hour in each direction (peak)
- Some services stop at Angmering, some pass through

### Known Issues
- **Long closure times**: Barriers frequently down for 5+ minutes, significant local complaint
- **Traffic congestion**: Queues on B2140 and surrounding roads
- **Worse since CCTV conversion**: Locals report longer closures since remote operation
- **Bus diversions**: Stagecoach 701 route diverted to avoid worst crossings
- **Parish council discussions**: Angmering Parish Council has raised concerns with Network Rail (see minutes from Sept 2024)
- **Safety incidents**: CCTV has caught cyclists getting stuck behind barriers (Network Rail media centre)
- **Barrier fault (2025)**: Reported stuck for 14+ hours (Sussex Express)
- **1965 fatal crash**: Historic incident at this crossing (Angmering Parish Council records)

---

## 📡 Data Sources & APIs

### Key Finding
> **There is NO public API that exposes live level crossing barrier state (open/closed).** Barrier status must be **inferred** from train position and route data. This is confirmed across all sources — Network Rail, NRE, community projects, the Level Crossing App, and our own exhaustive analysis of S-Class signalling data (see [NROD datasheet](nrod-datasheet/05-observations.md)). The LA Train Describer specification carries routes only (no LXG capability), and route SET events provide indirect barrier inference for MCB-CCTV crossings.

### Available Protocols (Network Rail datafeeds)
| Protocol | Port | Notes |
|---|---|---|
| STOMP | 61618 | Recommended, most documented |
| MQTT | 1883 | Community bridge |
| AMQP | 5672 | Alternative |
| WebSocket | 61614 | Browser-friendly |
| OpenWire | 61616 | Java/ActiveMQ native |

### 1. Network Rail Open Data (NROD) — Real-Time Train Movements
**The most promising approach for predicting crossing closures.**

| Detail | Info |
|---|---|
| **URL** | https://datafeeds.networkrail.co.uk / https://publicdatafeeds.networkrail.co.uk |
| **Access** | Free registration, limited to 1,000 users (first-come-first-served) |
| **Protocol** | STOMP (port 61618), also OpenWire and AMQP |
| **Data format** | JSON, gzip-compressed |
| **Auth** | Username/password from registration |
| **Durable subscriptions** | Yes (5 min message retention on disconnect) |

#### Relevant Feeds

**a) Train Describer (TD) Feed** ⭐ Most relevant
- Shows train headcodes stepping through berths (track sections)
- Real-time, second-by-second train position data
- Sussex Coastway TD area: **LA** (confirmed from SMART data)
- Topic: `/topic/TD_ALL_SIG_AREA`
- **Key insight**: By monitoring which TD berth a train is in, and knowing the "strike-in" berth (the point where barrier closure is triggered), you can predict when barriers will close

**b) Train Movements (TRUST) Feed**
- Arrival/departure/passing times at timing points
- Topic: `/topic/TRAIN_MVT_ALL_TOC`
- Less granular than TD but includes schedule vs actual times
- Useful for knowing when the next train is expected

**c) VSTP (Very Short Term Planning)**
- Schedule changes published at short notice
- Useful for knowing about engineering works / cancellations

#### NROD Feed Topics
| Topic | Description |
|---|---|
| `/topic/TRAIN_MVT_ALL_TOC` | Train movements (TRUST) — all operators |
| `/topic/TD_ALL_SIG_AREA` | Train Describer — all signal areas |
| `/topic/RTPPM_ALL` | Real-time performance measures |
| `/topic/VSTP_ALL` | Very short term planning |
| `/topic/TSR_ALL_ROUTE` | Temporary speed restrictions |

#### Static Feeds (HTTP GET, authenticated)
| Feed | Description |
|---|---|
| SCHEDULE | Daily CIF/JSON timetable extracts |
| SMART | TD berth offset data (maps berths to locations) |
| CORPUS | Location reference data (stations, timing points) |
| TPS | Detailed network model |
- ❌ No dedicated "Level Crossing Status" feed (open/closed)
- ❌ No direct barrier position data in public feeds
- The actual barrier control is done via signalling systems not exposed publicly

#### How to Predict Crossing Closure
1. **Get TD berth map** for the Angmering area (signalling diagrams)
2. **Identify the "strike-in" berth** — the track circuit that triggers the crossing sequence
3. **Monitor TD feed** for trains entering that berth
4. **Calculate**: `closure_time = distance_from_strike_in / train_speed + barrier_lowering_time`
5. Typical barrier lowering time: 10-30 seconds
6. Strike-in distance: typically 1.5-2 miles for 75mph lines

#### Connecting (Python example)
```python
import stomp
import gzip
import json

class Listener(stomp.ConnectionListener):
    def on_message(self, frame):
        body = gzip.decompress(frame.body)
        data = json.loads(body.decode('utf-8'))
        # Filter for Angmering area TD berths
        for msg in data:
            if msg.get('area_id') == 'LA':  # Angmering area
                print(msg)

conn = stomp.Connection([('publicdatafeeds.networkrail.co.uk', 61618)])
conn.set_listener('', Listener())
conn.start()
conn.connect('USERNAME', 'PASSWORD', wait=True)
conn.subscribe('/topic/TD_ALL_SIG_AREA', id=1, ack='auto')
```

### 2. National Rail Darwin API — Departure Boards
**Good for train times, not crossing status.**

| Detail | Info |
|---|---|
| **URL** | https://realtime.nationalrail.co.uk/OpenLDBWSRegistration/ |
| **Access** | Free registration, API token via email |
| **Protocol** | SOAP/XML (OpenLDBWS) or REST via Huxley2 proxy |
| **Rate limit** | 5 million requests per 4-week period (free tier) |
| **Data** | Live departure/arrival boards, delays, cancellations, platform info |

#### Useful for
- Knowing when the next train is due at Angmering
- "Train approaching in X minutes" notifications
- Delay/cancellation info

#### Huxley2 — REST proxy for Darwin
- GitHub: https://github.com/jpsingleton/Huxley2
- Converts SOAP to JSON REST
- Can self-host or use public instance (https://huxley2.azurewebsites.net)
- **Key endpoints:**
  - `GET /departures/ANG/10` — next 10 departures from Angmering
  - `GET /arrivals/ANG/10` — next 10 arrivals
  - `GET /all/ANG/10` — both
  - `GET /next/ANG/to/GBS` — next train Angmering → Goring
  - `GET /service/{serviceId}` — full service details
- All require `?accessToken=YOUR_TOKEN`

### 3. RealTimeTrains (RTT)
| Detail | Info |
|---|---|
| **URL** | https://www.realtimetrains.co.uk |
| **API** | https://api.rtt.io/api/v1 |
| **Access** | Free for personal use, registration required |
| **Data** | Detailed train movement data, actual vs planned times |

- Provides very detailed per-train timing at every location
- Can show approaching trains for Angmering
- JSON API available
- Useful as a secondary/validation source

### 4. Open Data — Level Crossing Register
| Detail | Info |
|---|---|
| **data.gov.uk** | https://www.data.gov.uk/dataset/7938802b-5b54-4989-bcd0-31629177445e/level-crossings-data |
| **Publisher** | Network Rail, OGL licence |
| **Content** | ~6,000 level crossings in UK |
| **Format** | Links to Network Rail safety page (not machine-readable CSV unfortunately) |

#### Other Crossing Databases
| Source | URL | Notes |
|---|---|---|
| levelcrossings.co.uk | https://levelcrossings.co.uk | UK & IoM directory, searchable, not live status |
| trainslive.uk | https://trainslive.uk/level-crossings/ | Searchable DB with type/risk/protection |
| ABC Railway Guide | https://abcrailwayguide.uk | Has specific Roundstone crossing page |
| RSSB Data Hub | https://www.rssb.co.uk/safety-and-health/level-crossings/level-crossing-data-hub | Member access only |
| Level Crossing App | https://levelcrossingapp.co.uk | States "no current live closure resource available" — still in dev |

### 5. NRE Darwin Feed Types
| Feed | Type | Description |
|---|---|---|
| LDB Webservice (PV) | JSON API | Public departure boards |
| LDB Webservice (Staff) | JSON API | Extended data (reasons, calling points) |
| Darwin Timetable | Push feed | Schedule data |
| Darwin Push Port | Push feed | Real-time updates via STOMP |
| HSP | JSON API | Historical Service Performance |
| KB API | JSON API | Knowledge Base (disruptions, incidents) |

### 6. Rail Data Marketplace (newer)
| Detail | Info |
|---|---|
| **URL** | https://marketplace.raildata.co.uk / https://raildata.org.uk |
| **Status** | Expanding — consolidating various Network Rail data products |
| **May include** | New dedicated feeds or data products for infrastructure status |

Worth monitoring for new crossing-specific data products.

### 6. OpenTrainTimes — Track Maps
| Detail | Info |
|---|---|
| **URL** | https://www.opentraintimes.com/maps |
| **What it shows** | Live signalling berth maps with train positions |
| **Useful for** | Identifying which TD berths are near Roundstone crossing |

- Free to view in browser
- Shows real-time train positions on schematic maps
- Can identify the exact berths you need to monitor

---

## 🏠 Home Assistant Integration

### Existing HA Integrations

#### 1. UK Rail / National Rail Times (HACS)
- **Repo**: https://github.com/crismc/homeassistant_nationalrailtimes_integration
- **What it does**: Sensors showing next departures from a station
- **Lovelace card**: https://github.com/crismc/homeassistant_nationalrailtimes_lovelace
- **Requires**: Darwin API token
- **Useful for**: "Next train from Angmering" sensor

#### 2. ha-uk-rail (HACS)
- **Repo**: https://github.com/michael-ellis/ha-uk-rail
- **Similar to above**: Departure board sensors
- **Config**: Station CRS codes (ANG for Angmering)

#### 3. My Rail Commute (HACS)
- **Repo**: https://github.com/adamf83/my-rail-commute
- **What it does**: Tracks specific commute routes
- **Updated**: April 2026 (active development)

### Custom Integration Approach

None of the existing integrations handle **level crossing prediction**. This would need to be custom-built:

#### Option A: NROD TD Feed → MQTT → HA
1. Python script subscribes to NROD TD feed via STOMP
2. Filters for Angmering area berths (ES prefix)
3. Detects train entering strike-in berth
4. Publishes prediction to local MQTT broker
5. HA listens via MQTT integration
6. Creates sensors: `binary_sensor.roundstone_crossing_active`, `sensor.roundstone_crossing_eta`

#### Option B: Darwin API → REST sensor → HA
1. Poll Darwin API every 30-60 seconds for Angmering departures
2. Parse approaching trains
3. Calculate ETA based on previous station departure time
4. Create HA template sensors for "train approaching" / "crossing likely closing soon"
5. Simpler but less accurate (no berth-level granularity)

#### Option C: Hybrid (recommended)
1. Use Darwin API for train schedule context (what trains are expected)
2. Use NROD TD feed for real-time berth stepping (when is the train actually near)
3. Combine both for accurate predictions
4. Publish to MQTT for HA consumption

### Notification Ideas
- 📱 Phone notification: "🚂 Train approaching Roundstone crossing in ~2 minutes"
- 🔴 HA dashboard indicator: Red/amber/green crossing status
- 🗣️ Jarvis voice: "Heads up, the crossing barriers are about to come down"
- 🪞 MagicMirror: Live crossing status widget
- ⏰ Morning routine: "The crossing is clear" or "Wait 3 minutes, train approaching"
- 📊 Statistics: Track average closure duration, busiest times, delays

---

## 🗺️ Key Reference Points

### Station Codes
| Station | CRS Code | TIPLOC | STANOX | Notes |
|---|---|---|---|---|
| Angmering | ANG | ANGMRNG | 87998 | West side of crossing |
| Goring-by-Sea | GBS | GORNGBS | 87997 | East side of crossing |
| Worthing | WRH | WRTHING | — | Major station east |
| Littlehampton | LIT | LTLHMPN | — | Junction station west |
| Ford | FOD | FORD | — | Junction for Arun Valley line |

### TD Berth Area
- Sussex Coastway TD area: **LA** (confirmed from SMART data — not ES as initially assumed)
- Berths near crossing:
  - **Goring side (eastbound approach)**: 0032, 0033, 0034, 0035
  - **Crossing zone**: 0036, 0037
  - **Angmering side (westbound approach)**: 0038, 0039, 0040, 0041
- S-class signalling: 7 addresses actively used in area LA (00–06). **All bits are route indicators (RTE)** — confirmed by wiki SOP and TD capability matrix. Address 07 has minimal activity (infrastructure indicator). See [NROD datasheet](nrod-datasheet/06-la-sop.md) for complete decode.

---

## 🔧 Technical Architecture (Final Design)

Based on rubber-duck review — key insight: MCB-CCTV crossings are signaller-controlled, so barrier timing is variable. Design around **inference + confidence**, not fixed timers.

```
NROD STOMP Feeds
├── TD (berth stepping) ──→ Primary: precise train position
└── TRUST (movements)   ──→ Secondary: train identity + lifecycle

CIF/VSTP Schedule ────────→ "What trains to expect today"

        │
        ▼
Python Service (roundstone-crossing)
│
├── Train Tracker / Correlator
│   └── Maintains per-train objects:
│       headcode, UID, direction, last_berth,
│       last_trust_event, speed_estimate, confidence
│
├── Crossing State Inferrer
│   └── Derives crossing state from active trains:
│       UNKNOWN → OPEN → CLOSING_PREDICTED →
│       CLOSED_INFERRED → OPENING_PREDICTED → OPEN
│       (+ STALE_DATA if feeds drop)
│
├── Timing Model (empirical, calibrated from observations)
│   └── NOT fixed "120s before arrival"
│   └── Learns actual timings per direction/service pattern
│
├── Historical Logger (SQLite)
│   ├── crossing_state_intervals (start, end, state, confidence)
│   ├── train_passages (train_id, direction, predicted/observed times)
│   └── raw_evidence (TD steps, TRUST events)
│
└── API (FastAPI)
    ├── GET /status — current inferred state + confidence + next change
    ├── GET /predictions — upcoming trains + predicted closure windows
    └── GET /history — query historical intervals
```

### Key Design Decisions
1. **TD primary, TRUST supplementary** — TD for precision near crossing, TRUST for train identity and fallback
2. **Train-object-based** — not a single state machine timer. Track each train independently, derive crossing state from the set
3. **Multiple simultaneous trains** — barriers stay down until ALL relevant trains clear
4. **Inferred states with confidence** — never claim to know barrier position authoritatively
5. **Empirical timing model** — start with heuristic (70mph × Xs), calibrate from observed data over time
6. **Configurable berth zones** — berth IDs in config file, not hardcoded
7. **UNKNOWN/STALE state** — if feeds drop, don't assume OPEN

---

## 📋 Next Steps

1. ~~Register for NROD access~~ ✅ Done
2. ~~Identify exact TD berths~~ ✅ Done (area LA, berths 0032-0041)
3. ~~Build Python service~~ ✅ Done — live tracker + inferrer + API + web dashboard
4. ~~Test predictions~~ ✅ Calibrated from 4 days of manual observations
5. **Identify SF barrier bit** — ✅ Resolved: barrier NOT in NROD. LA has RTE only, no LXG. Use route-based inference instead.
6. **Implement route-enhanced prediction** — Route SET events give 300–400s advance warning. 98.8% coverage validated.
6. **Build ESP32 device** — Parts list and firmware ready, needs assembly for continuous logging
7. **CIF schedule integration** — Advance prediction before trains appear on TD
8. **Home Assistant** — MQTT sensors, notifications, Jarvis voice, MagicMirror widget

### Registration Links
- Network Rail Open Data: https://publicdatafeeds.networkrail.co.uk
- National Rail Darwin: https://realtime.nationalrail.co.uk/OpenLDBWSRegistration/
- RealTimeTrains API: https://api.rtt.io
- Open Rail Data Wiki: https://wiki.openraildata.com
- Open Rail Data community: https://groups.google.com/g/openraildata-talk

### Useful GitHub Repos
- NROD Python STOMP example: https://github.com/openraildata/td-trust-example-python3
- NROD Go STOMP client: https://github.com/openraildata/stomp-client-go
- Huxley2 (Darwin REST proxy): https://github.com/jpsingleton/Huxley2
- HA National Rail integration: https://github.com/crismc/homeassistant_nationalrailtimes_integration
- HA UK Rail: https://github.com/adamf83/my-rail-commute
- Train departure screen (SSD1322): https://github.com/chrishutchinson/train-departure-screen

### Key Contacts
- Network Rail Open Data: opendata@networkrail.co.uk
- Angmering Parish Council: https://www.angmering-pc.gov.uk

---

## 📝 Notes
- No public API directly exposes "crossing open/closed" — this must be inferred from train positions and route data
- MCB-CCTV crossings like Roundstone are operated by a human signaller — route SET events confirm barriers are down (signaller must lower and verify via CCTV before setting route)
- LA TD spec has no LXG capability — barrier state is in local interlocking only, not fed to Train Describer
- Route-based inference validated: 98.8% coverage across 660 crossings, 35% get earlier warning than TD berth alone
- TD berth data is the gold standard for accuracy but requires more setup
- Darwin API is simpler but gives ~1-2 minute accuracy at best
- Could potentially log all closure events to build a historical dataset and find patterns
