# TODO — Roundstone Crossing Predictor

## 🔴 Before First Run (blockers)

- [ ] **Register for NROD access** — https://publicdatafeeds.networkrail.co.uk
  - Create account, get username/password
  - Add credentials to `.env` (copy from `.env.example`)
  - Note: limited to 1,000 users, first-come-first-served

- [ ] **Map TD berths near Roundstone crossing**
  - Go to https://www.opentraintimes.com/maps and find the Angmering area
  - Identify berth IDs for the "ES" TD area between Goring-by-Sea and Angmering
  - Categorise each berth into: approach / strike_in / at_crossing / clear (for both up and down directions)
  - Populate `config.yaml` berth zone arrays

- [ ] **Download SMART data** from NROD portal
  - This maps TD berths to physical locations (mileages, timing points)
  - Use it to confirm which berths are near the crossing
  - Also download CORPUS data (location reference)

## 🟡 Early Improvements (once running)

- [ ] **Calibrate timing model** — the initial heuristic is 120s pre-closure, 15s post-clearance, based on MCB-CCTV standards. Real Roundstone timings may differ. Use historical logged data to find actual averages per direction/service pattern and update `config.yaml`.

- [ ] **TRUST STANOX mapping** — current config uses TIPLOC for TRUST timing points, but TRUST movements use STANOX. Verify the STANOX values (87997 for Goring, 87998 for Angmering) match what the feed sends. May need to use CORPUS data to cross-reference.

- [ ] **Handle freight trains** — freight may not appear on Darwin/schedules. TD will still show them as headcodes. Ensure the tracker handles unknown headcodes gracefully (it should — just lower confidence).

- [ ] **Validate predictions** — manually observe the crossing a few times and compare predicted vs actual barrier times. Adjust timing model based on findings.

- [ ] **Add schedule context** — download daily CIF schedule to know what trains to expect. This lets us predict closures even before the train appears on TD. Not essential for v1 but improves the "what's coming in the next hour" view.

## 🟢 Future Enhancements

- [ ] **Home Assistant integration** — publish state to MQTT, create HA sensors, notifications, Jarvis voice announcements, MagicMirror widget
- [ ] **Web dashboard** — simple HTML page showing live crossing status, countdown, next trains
- [ ] **Historical analytics** — average closure duration by hour/day, busiest times, longest closures
- [ ] **Push notifications** — "crossing closing in 2 minutes" via HA, Telegram, or similar
- [ ] **Multi-crossing support** — the architecture is generic enough to support other local crossings (Angmering Station Road crossing, etc.) — just add berth zones to config
- [ ] **Empirical timing auto-calibration** — automatically adjust timing model from observed data (compare predicted vs actual state change times)
- [ ] **GitHub repo** — push to `benSlaughter/roundstone-crossing` once stable
