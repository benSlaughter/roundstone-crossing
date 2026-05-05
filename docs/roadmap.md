# Roadmap — Roundstone Crossing Predictor

A living roadmap for the project. This is a hobby project — timelines are aspirational, not commitments. Priorities shift based on what's interesting, what's useful, and what breaks.

---

## 🟡 Short-term — Next Few Weeks

Quality, polish, and reliability. Make what exists work better.

| | Item | Why |
|---|---|---|
| ~~🧪~~ | ~~**Test coverage: `rtt.py`**~~ | ✅ Done — `test_rtt.py` added |
| ~~🧪~~ | ~~**Test coverage: `feed.py`**~~ | ✅ Done — coverage improved |
| 🔒 | **Rate limiting** | Protect the public-facing API from abuse — basic middleware on FastAPI |
| ~~🔒~~ | ~~**CORS policy**~~ | ✅ Done — no CORS headers = browsers block cross-origin by default (confirmed in audit) |
| ~~🔒~~ | ~~**CSP headers**~~ | ✅ Done — Content-Security-Policy middleware added, inline styles removed |
| 🎨 | **Custom error pages** | Styled 404/500 pages matching the dark theme instead of bare FastAPI defaults |
| 🎨 | **Logo and branding** | Give the project a visual identity — explore AI-generated logo options |
| ~~🔧~~ | ~~**Tech debt cleanup**~~ | ✅ Done — window merging extracted, predictions endpoint broken up, _handle_td refactored |
| 🎨 | **Feedback form polish** | Improve the modal UX, add confirmation toast, maybe a character counter |

---

## 🟠 Medium-term — 1–3 Months

New features and prediction accuracy. Make it smarter and more useful.

| | Item | Why |
|---|---|---|
| 🔧 | **ESP32 device build** | Parts list and firmware are ready (~£21 BOM) — assemble and deploy for continuous ground-truth barrier logging |
| 📊 | **Prediction accuracy tracking** | Log predicted vs actual closure times to measure and improve model accuracy over time |
| 🎨 | **Live countdown timers** | Ticking countdowns on prediction cards instead of static "X min" — much more engaging UX |
| 📊 | **Historical analytics & charts** | Average closure duration by hour/day, busiest times, longest closures — surface patterns in the data |
| 🎨 | **Mobile-responsive layout** | People check crossing status on their phone walking to the station — mobile UX matters most |
| 🔧 | **`--no-feed` dev mode** | Run the app locally without an NROD STOMP connection for faster development iteration |
| 📊 | **SF signal correlation** | Revisit barrier state detection from S-Class signalling data — may need broader area capture beyond LA |
| 🔧 | **Home Assistant integration** | Publish barrier state to MQTT — enables HA sensors, automations, Jarvis voice announcements |
| 📊 | **CIF schedule integration** | Download daily CIF schedules for advance predictions — know about trains before they appear on TD |
| 🔧 | **Freight train handling** | Improve confidence for freight that doesn't appear in passenger schedules — TD shows headcodes but no RTT enrichment |

---

## 🟢 Long-term — 6+ Months

Scale, community, and ambition. Make it useful beyond one person.

| | Item | Why |
|---|---|---|
| 🔧 | **Multi-crossing support** | Architecture already supports it — add Angmering Station Road and other local crossings via config |
| 📢 | **Push notifications** | "Crossing closing in 2 minutes" via Telegram, Home Assistant, or web push — the killer feature for locals |
| 📊 | **Auto-calibration from ESP32** | Automatically adjust timing model parameters from device-logged vs predicted state changes |
| 👥 | **Community reporting** | Let local users report barrier state via the web — crowdsourced ground truth |
| 🔧 | **Public API** | Documented REST API for third-party integrations, other apps, local transport tools |
| 🎨 | **Progressive Web App (PWA)** | Installable on mobile with offline support and push notifications |
| 🤖 | **ML-based prediction model** | Train a model on historical data — learn patterns that rule-based timing can't capture (e.g. time-of-day variance, signaller behaviour) |
| 📊 | **Observation upload endpoint** | API to accept CSVs from ESP32 or phone shortcuts for automated accuracy comparison |

---

## 🔵 Technical Vision

Architecture, testing, and infrastructure improvements that underpin everything else.

### Testing Strategy
| | Item | Why |
|---|---|---|
| 🧪 | **Integration tests** | Test the full pipeline (feed → tracker → inferrer → API) with realistic message sequences |
| 🧪 | **End-to-end tests** | Browser-based tests for the dashboard — verify tabs, auto-refresh, prediction cards render correctly |
| 🧪 | **Replay-based testing** | Record real NROD message sequences and replay them in tests for realistic coverage |

### Architecture
| | Item | Why |
|---|---|---|
| 🔧 | **Structured logging** | Replace print-based logging with structured JSON logs — easier to search and filter in production |
| 🔧 | **Connection pooling** | Pool HTTP connections to RTT API and manage STOMP reconnection more robustly |
| 🔧 | **Config validation** | Validate `config.yaml` at startup with clear error messages — catch misconfigurations before they cause silent failures |
| 🔧 | **Event bus / pub-sub** | Decouple components with an internal event system — cleaner than direct function calls between modules |

### Deployment & Operations
| | Item | Why |
|---|---|---|
| 🔧 | **Health check endpoint** | `/health` returning feed age, train count, last state change — useful for monitoring and Docker health checks |
| 📊 | **Monitoring & alerting** | Track feed uptime, prediction count, error rate — know when something breaks before users do |
| 🔧 | **Automated backups** | SQLite database backup to cloud storage — history data is valuable and hard to recreate |
| 📊 | **Accuracy metrics dashboard** | Dedicated page showing prediction accuracy over time — builds trust and guides calibration |

---

## Guiding Principles

- **Reliability over features** — a prediction that's always available beats a fancy one that crashes
- **Confidence, not certainty** — never claim to know barrier state, only infer it with a score
- **Local-first** — this serves people walking to Angmering station, not enterprise customers
- **Observable** — if it's running, you should be able to see what it's doing and how well it's doing it
- **Fun** — this is a hobby project; if it stops being interesting, the priorities should change

---

*Last updated: May 2026*
