# TODO — Roundstone Crossing Predictor

## ✅ Completed

- [x] **Register for NROD access** — Connected and receiving live TD + TRUST + SF data
- [x] **Map TD berths near Roundstone crossing** — Area LA confirmed; berths 0032-0042 (UP/even) and 0033-0041 + A027 (DOWN/odd) mapped
- [x] **Download SMART data** — SMART and CORPUS data used for berth-to-location mapping
- [x] **Calibrate timing model** — Calibrated from production data: pre_closure=180s (was 120s, refined from 10-day route-SET-to-at-crossing median), TRUST offsets recalibrated against SMART (UP 56s, DOWN 121s)
- [x] **Validate predictions** — Observed multiple crossing events, identified and fixed state bouncing, stale train, and false open issues
- [x] **Automated tests** — 332 tests covering inferrer, tracker, feed, API, history, route monitor, security headers
- [x] **Opening prediction** — Multi-train closure window merging with accurate opening predictions
- [x] **Web dashboard** — Tab-based UI with CSS/JS extracted to separate files
- [x] **Code audit remediation** — Thread safety, feed reconnect, CB_MSG handling, XSS fixes; constant-time admin auth; full security-header set
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
- [x] **Berth direction correction** — A027 is DOWN-only (clear berth); 0042 is UP-approach. LA convention: even=UP, odd=DOWN
- [x] **Geography correction** — Roundstone is WEST of Goring, EAST of Angmering (~885m east of Angmering platform)
- [x] **Route-hold cap** — Stuck routes downgrade to UNKNOWN after 15 min instead of locking us in CLOSED
- [x] **OPENING via route-clear** — When all routes clear with no train, briefly emit OPENING_PREDICTED before OPEN
- [x] **State reason field** — Every state transition records why; visible in `/live` and persisted in `state_intervals.reason`
- [x] **Wider TLS hardening** — CSP + X-Content-Type-Options + X-Frame-Options + Referrer-Policy + Permissions-Policy

## 🔲 Next Up

- [ ] **Restart blind spot** — After a process/container restart, in-memory tracker state is lost. Trains currently in our zone but not currently moving (e.g. dwelling at a station) are invisible until they next step or RTT picks them up. Replay recent `train_events` from the DB on startup to rebuild tracker state, OR use `train_snapshots` to restore the most recent active set.
- [ ] **Nightly auto-restart cron** — Schedule a cron job on the server (around 01:00, in the no-train window) to pull the latest image and restart the container. Lets minor non-urgent fixes deploy automatically with no daytime disruption. Pairs well with the restart-blind-spot fix above so we don't lose any tracking on auto-restart.
- [ ] **Custom error pages** — Styled 404, 500, etc. pages matching the site's dark theme
- [ ] **Logo/branding** — Research AI tooling for creating a unique custom logo
- [ ] **State-coverage metric** — Build a metric measuring "% of time predictor correctly says CLOSED during actual closures" so we can fairly evaluate route-based vs no-route inference. Required before re-enabling route inference.
- [ ] **Re-enable route inference (after metric)** — Currently DISABLED in production (`config.yaml` `inference.use_routes: false`) after 2026-05-08 false-positive regression. Routes still monitored and shown on `/live`. Needs per-route reliability weighting and validation against ground-truth.
- [ ] **Build ESP32 device** — Parts list ready (~£21 BOM), firmware written. Order parts and assemble for continuous ground-truth logging.
- [ ] **Handle freight trains** — Freight may not appear in schedules. TD shows them as headcodes. Tracker handles unknown headcodes but confidence could be improved.
- [ ] **Add schedule context (CIF)** — Download daily CIF schedule to predict closures before trains appear on TD. Improves the "next hour" view.
- [ ] **Refactor large modules** — `src/api.py` (655 lines) and `src/inferrer.update()` (large branching method) flagged in May 2026 audit; partly addressed but more decomposition would help. See `docs/quality-audit.md`.

## 🟢 Future Enhancements

- [ ] **Live countdown timers** — Ticking countdown on prediction cards instead of static "X min"
- [ ] **Prediction accuracy tracking** — Log predicted vs actual closures to measure accuracy (largely subsumed by the state-coverage metric above)
- [ ] **`--no-feed` mode** — Run dev without NROD STOMP for local development
- [ ] **Home Assistant integration** — Publish state to MQTT, create HA sensors, notifications, Jarvis voice announcements
- [ ] **Observation upload endpoint** — API to accept CSVs from ESP32 device or phone shortcuts for automated comparison
- [ ] **Historical analytics** — Average closure duration by hour/day, busiest times, longest closures
- [ ] **Push notifications** — "Crossing closing in 2 minutes" via HA, Telegram, or similar
- [ ] **Empirical timing auto-calibration** — Automatically adjust timing model from device-logged vs predicted state changes
- [ ] **Multi-crossing support** — Architecture supports other local crossings (e.g. Angmering Station Road) — just add berth zones to config
- [ ] **Mobile-friendly layout** — Responsive tweaks for checking on phone
