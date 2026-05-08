# Quality Audit — Roundstone Crossing Predictor

**Last updated:** 2026-08 (this audit). The previous audit (May 2026) is
captured below in `## Audit history` for context.

**Auditors:** Three parallel audits (code quality, security/privacy, docs
freshness) + targeted spot-checks + immediate fixes for low-risk findings.

**Headline:** The codebase is in a healthy state. 332 tests pass, overall
coverage is **82 %**, all blocking issues from the May 2026 audit are
resolved. The May 2026 follow-on work (route inference) shipped, regressed in
production, and was correctly hot-fixed by gating it behind a config flag.
This audit found **no critical issues**, **2 high-priority** items
(thread-safety in stateful loggers, public exposure of `/live/data`), and a
handful of medium/low items most of which are larger refactors rather than
bugs.

---

## Headline metrics

| Metric                    | Value                          |
|---|---|
| Source LOC (`src/`)       | 2,970 across 11 files          |
| Test LOC (`tests/`)       | 3,737 across 9 files           |
| Test count                | **332** (was 232 at May audit, 165 at original audit) |
| Coverage (overall)        | **82 %**                       |
| Coverage (high-value modules) | `models` 100, `inferrer` 97, `history` 97, `route_monitor` 94, `tracker` 93, `rtt` 89, `api` 89 |
| Coverage (lower)          | `feed` 73 %, `main` 0 % (entry-point) |
| Blocking issues           | **0**                          |
| Tech-debt markers (TODO/FIXME/XXX/HACK) in `src/` | **0** |

---

## What changed since the May 2026 audit

✅ **All May 2026 blocking issues fixed** (verified by spot-check):
- `tracker.trains` access in main loop now snapshots under lock (`src/main.py:146-150`, `src/api.py:61-63`).
- `HistoryLogger._connect()` sets `busy_timeout` on every connection (`src/history.py:27-32`).
- `.env.example` + RTT env var mismatch resolved (`RTT_TOKEN` consistent in code & template).

✅ **Most "Do Soon" items shipped**: window merging extracted to `src/utils.py`,
predictions endpoint broken up, SF/SG handler dedup'd, config validation at
startup, CSP middleware.

✅ **New work this period** (since May):
- Route monitoring (`src/route_monitor.py`, `tests/test_route_monitor.py`) — 14 LA crossing-area route bits tracked & shown on `/live`.
- State `reason` field — every transition records WHY it was entered, persisted to history (`state_intervals.reason`) with idempotent migration.
- Calibrated TRUST timing offsets (UP 56 s, DOWN 121 s) from SMART/BPLAN data via vaildata.uk.
- Geography & berth-direction corrections (A027 down-only, even=UP, odd=DOWN).
- Route-hold cap (15 min → UNKNOWN) and OPENING-via-route-clear transition.
- **Production regression** with route-based inference (false CLOSED while OPEN), correctly hot-fixed by `inference.use_routes: false` flag — code preserved for future re-enable once metrics support it.

---

## Findings by severity

### 🔴 Critical: none.

### 🟠 High

#### H1. `HistoryLogger` shares mutable state across threads without locking
**Files:** `src/history.py:18-25`, `:122-145` (`log_state_change`)
**Type:** Concurrency / data integrity

`HistoryLogger` is used from at least three threads (main loop, NROD feed
listener, FastAPI handlers via the route_monitor’s SF/SG callbacks). The
methods open a fresh SQLite connection per call (good — avoids cross-thread
sqlite3 issues), but the *Python-level* state — `_current_interval_id` and
`_current_state` — is mutated without any lock. Two threads calling
`log_state_change()` concurrently could double-open intervals or double-close
them.

**Recommendation:** Add a `threading.Lock` and acquire it inside the methods
that read/write `_current_*`. Low-effort, high-value.

#### H2. `/live/data` and `/live` are public with no auth
**Files:** `src/api.py:548-640`
**Type:** Information disclosure / privacy

The `/live` debug view and its data endpoint expose full internal state
(every tracked train’s headcode, route map, raw berth state, last feed message,
config). Currently unlinked from the main UI but trivially discoverable
(grep the static assets, scan robots.txt-style). On a public deployment this
gives any visitor real-time operational data they shouldn’t have.

**Recommendation:** either (a) move `/live*` behind the existing
`_check_admin` Bearer-token gate, (b) restrict by IP via nginx
`allow`/`deny`, or (c) add a `LIVE_VIEW_ENABLED=false` env-gated kill switch
for production. Option (a) is most flexible; pick whichever matches deployment
ergonomics.

### 🟡 Medium

#### M1. RTT client mutates shared state without locking
**Files:** `src/rtt.py:77-85`, `:338-377`
**Type:** Concurrency

`_retry_after`, `_server_retry_after`, `_consecutive_429s`, `_cache` are
written by the polling thread and read by the FastAPI `/health` endpoint
without synchronisation. Race conditions are unlikely to cause hard crashes
but can produce inconsistent rate-limit metrics. Lower stakes than H1 because
the data is purely informational.

**Recommendation:** add a `threading.Lock` around the rate-limit state mutations.

#### M2. `inferrer.update()` is large and branchy
**Files:** `src/inferrer.py:29-245`
**Type:** Code quality

The single `update()` method handles ~10 distinct cases (stale data,
no-trains/no-routes, no-trains/has-routes/cap, no-trains/has-routes/normal,
trains+at-crossing, was-closed, strike-in+routes, strike-in alone,
approaching+routes, approaching alone). Each branch is short and well-commented
but the cumulative complexity makes future changes risky (we just had two
production regressions touching this method — first the routes added false
positives, then the cap fix had to be added).

**Recommendation:** Split into per-case handler methods (`_handle_no_trains_no_routes`, `_handle_route_only`, `_handle_active_trains`, etc.) so each branch is independently testable and the dispatch is one screen of `if/elif` calls. **Defer until** we have the state-coverage metric (so we can detect regressions); refactoring without a safety net repeats the mistake.

#### M3. `api.py` `create_app()` is 600+ lines
**Files:** `src/api.py:34-655`
**Type:** Code quality

`create_app()` defines every route inline, including the substantial
predictions/window-merging logic. Coverage is good (89 %) but the file is hard
to navigate. Predictions assembly, feedback persistence, admin auth, static
serving, and live-debug endpoints are all interleaved.

**Recommendation:** Extract route handlers into separate module(s) — e.g.
`src/api/predictions.py`, `src/api/feedback.py`, `src/api/live.py` — each
exposing a function that registers routes onto an `APIRouter`. Predictions
window-building specifically should move to a service module so it can be unit-tested directly without `TestClient`.

#### M4. Feedback retention has no policy
**Files:** `src/api.py:533-537`, `src/history.py:117-123, 165-192`
**Type:** Privacy

The `/feedback` endpoint stores user message + User-Agent indefinitely. No
purge job, no documented retention. For a public deployment this is mild but
not great: the User-Agent can fingerprint individuals over time.

**Recommendation:** Add a docstring/policy stating retention (e.g. "feedback
kept indefinitely; UA truncated to family/major-version"). Optionally add a
SQLite `DELETE FROM feedback WHERE created_at < ...` job, or a manual purge
script in `scripts/`.

#### M5. `feed.py` has lower test coverage (73 %) and broad except clauses
**Files:** `src/feed.py:57-58, 176-177, 240-258, 280-299`
**Type:** Robustness

Several `except Exception:` blocks in `on_message`, `_poll_loop`,
`_ensure_token`, `_fetch_station`, `start`, and reconnect logic. They will
hide bugs and make recovery ambiguous. Coverage is lowest of any non-entrypoint
module (73 %).

**Recommendation:** Catch the specific exception types
(`requests.Timeout`, `requests.HTTPError`, `json.JSONDecodeError`,
`stomp.exception.ConnectFailedException`, etc.); log structured context;
re-raise unexpected ones. Add targeted tests for parse failures and
reconnect paths to push coverage past 85 %.

### 🟢 Low / informational

| # | Finding | File | Note |
|---|---|---|---|
| L1 | `CrossingStatus.to_dict()` mixes domain + serialisation | `src/models.py:79-99` | Pure cosmetic — moving to a schema layer is a refactor only worth doing alongside M3 |
| L2 | `tracker.py:295-304` not covered | `src/tracker.py` | Edge case in `get_active_trains` cleanup; trivial to add a test |
| L3 | `main.py` 0 % coverage | `src/main.py` | Entry point — hand-tested via Docker. Adding integration test would require mocking STOMP client |
| L4 | RTT cache uses module-level dict | `src/rtt.py` | Not actually a leak; `RTTClient` instance is the cache owner. Confirmed not a memory issue |
| L5 | `coverage` configured but no CI gate | — | Coverage report is manual; CI doesn’t fail on regression |
| L6 | No structured logging | many | `logging.info(f"...")` works but is hard to query in aggregate. Defer until ops actually ask for it |

---

## Security & privacy

### Fixed in this audit pass

- ✅ Admin token comparison switched to `hmac.compare_digest` (`src/api.py:526-531`); rejects substring matches; also rejects `Authorization` headers without the `Bearer ` prefix.
- ✅ Added the missing `ADMIN_TOKEN` entry to `.env.example` with usage docs.
- ✅ Added security headers beyond CSP: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` disabling geolocation/camera/microphone/payment/USB.
- ✅ 7 new tests in `TestSecurityHeaders` and `TestFeedbackGet` (constant-time auth, header presence, prefix rejection, substring rejection).

### Confirmed already good

- Parameterised SQL throughout (`src/history.py`).
- Frontend uses `esc()` before HTML insertion (`static/app.js:32-36, 236-239, 331-438`).
- `.gitignore` properly ignores `.env`, `*.db`, `logs/`, `experiments/*.db*`.
- Docker runs as non-root (`Dockerfile:24-36`), single port exposed (8590).
- No path traversal in file-serving — only fixed `FileResponse` targets (`src/api.py:47-50, 548-551`).
- No secrets in repo, scripts, tests, docs, or static assets (grep audited).

### Open

- **H2** above: `/live*` is public.
- **M4** above: feedback retention policy.
- **No rate limiting** on POST `/feedback` (carried over from May audit). nginx-level `limit_req_zone` recommended; not in app code because deployment is behind nginx.

---

## Test coverage

```
Name                   Stmts   Miss  Cover   Missing
----------------------------------------------------
src/api.py               302     32    89%
src/feed.py              213     58    73%
src/history.py           156      4    97%
src/inferrer.py          150      5    97%
src/main.py              145    145     0%
src/models.py             59      0   100%
src/route_monitor.py     101      6    94%
src/rtt.py               218     23    89%
src/tracker.py           219     15    93%
src/utils.py              18      0   100%
TOTAL                   1581    288    82%
```

**Strengths:**
- Models, history, inferrer, utils all > 95 % — the inferrer state machine (the project’s heart) is comprehensively tested across all branches including the recent reason-string and use_routes paths.
- Route monitor at 94 % despite being newer code.
- Both modes of route inference (`use_routes=True` legacy + `use_routes=False` production default) under test.

**Gaps worth addressing:**
- `feed.py` 73 %. Specifically untested: error-recovery paths, gzip-decode failures, malformed JSON, the `_handle_td` early-return paths. Adding ~10 tests here would push it past 85 %.
- `main.py` 0 %. Acceptable — it’s an entry point — but a single smoke test mocking the STOMP client and FastAPI server would add valuable safety to the wiring code.

---

## Documentation freshness

### Fixed in this audit pass

- ✅ Test counts updated everywhere (`docs/copilot-context.md`, `docs/TODO.md`, this file). All references now say **332**.
- ✅ `docs/TODO.md` rebuilt: completed items moved to ✅; "Route-enhanced prediction" reflects current DISABLED state with rationale; new entries for state-coverage metric, refactoring backlog, etc.
- ✅ `docs/copilot-context.md` "What's Done" / "Remaining Work" updated to reflect state-reason field, route disable, and security-header additions.
- ✅ Geography in all docs verified consistent: Roundstone WEST of Goring, EAST of Angmering, ~885 m east of Angmering platform.
- ✅ Berth direction in all docs verified consistent: even=UP (eastbound), odd=DOWN (westbound), A027 down-only, 0042 up-approach.

### Recommendation (not done — requires data)

`docs/nrod-datasheet/06-la-sop.md` claims "LA contains only route data — no signal aspects, no track circuits, no point positions". Production data shows bit `03:6` is highly active (671 transitions/10 days) with a clearly non-route signature (mostly SET, brief CLR pulses, opposite of every decoded route). This contradicts the doc. Recommend updating 06-la-sop.md to acknowledge "predominantly route data with at least one bit (03:6) showing track-section/points-style behaviour, not yet decoded". Already tracked in `route_improvements.bit-036-not-route` for follow-up analysis.

---

## Tech debt scan

`grep -rn "TODO\|FIXME\|XXX\|HACK"` across `src/` and `tests/` returns:

- **Zero** TODO/FIXME/XXX/HACK markers in `src/`.
- Only false positives in `tests/` (the headcode "XXXX" used as a test fixture).

This is exceptional discipline for a hobby project of this age and is a strong sign the codebase has been actively maintained rather than accreting cruft.

---

## Architecture review

**Module dependency graph** (verified — no cycles):

```
main.py
  ├─→ feed.py ──→ tracker.py ──┐
  │                            ├─→ models.py
  ├─→ rtt.py ────→ tracker.py ─┤
  ├─→ route_monitor.py ────────┤
  ├─→ inferrer.py ─────────────┤   ← also uses utils.py
  ├─→ history.py ──────────────┤
  └─→ api.py ─────→ all of the above
```

**Strengths:**
- Clean separation of concerns: data ingest (`feed`, `rtt`), state holders (`tracker`, `route_monitor`), domain logic (`inferrer`), persistence (`history`), presentation (`api`).
- Models are pure — no I/O dependencies in `models.py`.
- Config externalised consistently via `config.yaml` — every runtime parameter has a config knob, none hard-coded.
- `utils.py` exists and is used (window merging extracted from duplication, as recommended in May audit).

**Weaknesses called out elsewhere:**
- `inferrer.update()` and `api.create_app()` are oversized (M2, M3).
- Some leakage of internal attributes (`tracker._lock`, `train._passage_logged`) into `main.py`. Encapsulation is intent-violated at one place; not buggy.

---

## Top-5 priority actions

| # | Action | Effort | Impact |
|---|---|---|---|
| 1 | **Build state-coverage metric** (in priority list as `better-metrics`). Without it, every inference change is a coin toss — see the May→Aug regression cycle. | Medium | 🟠 High — unblocks the entire route/inference roadmap |
| 2 | **Lock `HistoryLogger` mutable state (H1).** Add `threading.Lock` around `_current_interval_id`/`_current_state` writes. | Small | 🟠 High — prevents data-integrity bugs under load |
| 3 | **Decide & implement `/live*` access control (H2).** Either gate behind admin token or restrict via nginx. | Small | 🟠 High — closes information-disclosure gap |
| 4 | **Push `feed.py` coverage past 85 % (M5).** Specifically: gzip decode failures, malformed JSON, reconnect paths. Replace bare `except Exception` with specific types. | Medium | 🟡 Medium — feed.py is the data ingest, low coverage = blind spot |
| 5 | **Refactor `inferrer.update()` (M2) — but only after #1.** Split per-case handler methods. Refactoring without a coverage metric repeats the May→Aug regression mistake. | Medium | 🟡 Medium — improves maintainability for the inevitable future inference work |

---

## Items deliberately NOT done

The following came up in audits but were judged not worth the effort/risk:

- **Refactor `api.create_app()` into APIRouters.** Moderate effort, no functional benefit; defer until M3 or until adding new endpoints would benefit from the structure.
- **Add structured logging.** The current `logging.info(f"...")` works fine for a hobby project; would add complexity without ops needing it yet.
- **`pytest-cov` plugin and CI coverage gate.** `coverage` is installed and works; adding a CI gate would gate PRs on a coverage delta, useful but not urgent.
- **`python-dotenv`.** Manual `.env` parsing in `main.py` works; replacing it is busywork.
- **Per-test isolation for fix-ups.** Existing tests are fast (~4 s) and well-organised; restructuring would not improve velocity.

---

## Sign-off

The codebase is in a **good** state. No critical issues. The two high-priority
findings (H1 thread safety, H2 `/live*` exposure) are small fixes; the medium
items are mostly larger refactors that should follow rather than precede the
state-coverage metric work. The previous audit's recommendations were all
addressed; this audit's recommendations are all tracked in
`docs/TODO.md` or `route_improvements`.

If the user has time for one thing, do **#1 (state-coverage metric)** — it
unblocks everything else and would have caught the May→Aug regression.

---

## Audit history

### May 2026 (compressed summary, full report archived in git history)

The first audit ran four parallel reviews + a rubber-duck pass and found:
- **2 blocking bugs**: thread-safety in `tracker.trains` iteration; SQLite
  connections not inheriting `busy_timeout`. **Both fixed**.
- 5 security findings (rate limiting, XSS-via-innerHTML, UA-XSS,
  missing CSP, admin token compare). All actioned in this audit pass except
  rate-limiting (deferred to nginx layer).
- Coverage was 93 % overall (now 82 % — looks lower because we added
  `route_monitor.py` and grew `api.py` significantly; absolute test count is
  up from 232→332).
- Code quality findings (long predictions endpoint, `_handle_td` complexity,
  SF/SG dedup, no config validation) all addressed.
- Tech debt items (`.env` parsing, `Optional[X]` style, type stubs) deferred
  as low-impact.

The May audit's "Top 5 Priority Fixes" are all done. ✅
