# TODO — Roundstone Crossing Predictor

## ✅ Completed

- [x] **Register for NROD access** — Connected and receiving live TD + TRUST + SF data
- [x] **Map TD berths near Roundstone crossing** — Area LA confirmed, berths 0032-0041 mapped for both directions
- [x] **Download SMART data** — SMART and CORPUS data used for berth-to-location mapping
- [x] **Calibrate timing model** — Calibrated from 4 days of manual observations (Apr 28-May 1): pre_closure=120s, crossing_clearance=10s, post_clearance=5s
- [x] **Validate predictions** — Observed multiple crossing events, identified and fixed state bouncing, stale train, and false open issues
- [x] **Automated tests** — 165 tests covering inferrer, tracker, feed, API, and history
- [x] **Opening prediction** — Multi-train closure window merging with accurate opening predictions
- [x] **Web dashboard** — Tab-based UI with CSS/JS extracted to separate files
- [x] **Code audit remediation** — Thread safety, feed reconnect, CB_MSG handling, XSS fixes
- [x] **GitHub repo** — Public at `benSlaughter/roundstone-crossing`
- [x] **Security audit** — Credentials, PII, .gitignore cleaned
- [x] **RTT integration** — Platform-level train enrichment at Angmering and Goring-by-Sea
- [x] **S-Class message logging** — SF/SG/SH/CT messages recorded to SQLite for analysis
- [x] **ESP32 barrier logger** — Firmware, docs, schematics, and BOM ready
- [x] **Predictions tab** — Upcoming crossing closure windows from RTT with proximity-coloured cards
- [x] **Docker deployment** — Multi-stage Dockerfile, docker-compose, CI/CD via GitHub Actions
- [x] **Production deployment** — Live at crossing.benslaughter.com (nginx + SSL + Docker on Azure)
- [x] **Feedback form** — Modal in footer, SQLite storage, admin-protected read endpoint
- [x] **UTC/BST timezone fix** — RTT times correctly handled as Europe/London

## 🔲 Next Up

- [ ] **Custom error pages** — Styled 404, 500, etc. pages matching the site's dark theme
- [ ] **Logo/branding** — Research AI tooling for creating a unique custom logo
- [x] **SF barrier bit identification** — ✅ RESOLVED: Barrier state is NOT in NROD data (LA TD spec has no LXG capability, exhaustive search of all 64 LA bits confirmed, complete SOP decode shows routes only). Use route-based inference instead — route SET near crossing implies barriers down (MCB-CCTV signaller procedure). Validated: 98.8% coverage across 660 crossings over 9 days.
- [ ] **Route-enhanced prediction** — Implement route-based barrier inference in the predictor. LA route SET events (R27, R28, R29, R31, R32–R35, RA007, RA008, RA010) provide 300–400s median advance warning. 35% of crossings get earlier warning than TD berth alone.
- [ ] **Build ESP32 device** — Parts list ready (~£21 BOM), firmware written. Order parts and assemble for continuous ground-truth logging.
- [ ] **Handle freight trains** — Freight may not appear in schedules. TD shows them as headcodes. Tracker handles unknown headcodes but confidence could be improved.
- [ ] **Add schedule context (CIF)** — Download daily CIF schedule to predict closures before trains appear on TD. Improves the "next hour" view.

## 🟢 Future Enhancements

- [ ] **Live countdown timers** — Ticking countdown on prediction cards instead of static "X min"
- [ ] **Prediction accuracy tracking** — Log predicted vs actual closures to measure accuracy
- [ ] **`--no-feed` mode** — Run dev without NROD STOMP for local development
- [ ] **Home Assistant integration** — Publish state to MQTT, create HA sensors, notifications, Jarvis voice announcements
- [ ] **Observation upload endpoint** — API to accept CSVs from ESP32 device or phone shortcuts for automated comparison
- [ ] **Historical analytics** — Average closure duration by hour/day, busiest times, longest closures
- [ ] **Push notifications** — "Crossing closing in 2 minutes" via HA, Telegram, or similar
- [ ] **Empirical timing auto-calibration** — Automatically adjust timing model from device-logged vs predicted state changes
- [ ] **Multi-crossing support** — Architecture supports other local crossings (e.g. Angmering Station Road) — just add berth zones to config
- [ ] **Mobile-friendly layout** — Responsive tweaks for checking on phone
