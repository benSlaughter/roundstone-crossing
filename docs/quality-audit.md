# Quality Audit — Roundstone Crossing Predictor

**Date:** May 2026
**Auditors:** 4 parallel audits (security, test coverage, code quality, tech debt) + rubber duck / devil's advocate review
**Summary:** Well-structured hobby project with strong domain modelling and 93% test coverage. Two blocking reliability bugs found (thread safety, SQLite timeout). Several medium-priority improvements identified. No catastrophic design failures.

---

## Executive Summary

Roundstone Crossing Predictor is a well-engineered hobby project with clean separation of concerns across modules (`models` / `tracker` / `inferrer` / `feed` / `history` / `rtt` / `api`). The codebase demonstrates good practices: dataclass/enum domain modelling, config externalisation, parameterised SQL, and proper reconnection backoff.

| Metric | Value |
|---|---|
| Test coverage | 93% overall, 165 tests passing |
| Blocking issues | 2 (thread safety, SQLite timeout) |
| Security vulnerabilities | 0 critical, 2 medium, 1 high (rate limiting) |
| False positives filtered | 3 (CORS, esc() sufficiency, import caching) |

**Bottom line:** Fix the two blocking reliability bugs, add missing RTT tests, and address rate limiting. Everything else is incremental improvement.

---

## 1. 🔴 Blocking Issues (Must Fix)

These are real runtime reliability risks identified during rubber duck review.

### 1.1 Unsynchronised Iteration over `tracker.trains`

| | |
|---|---|
| **File** | `src/main.py:97-101` |
| **Severity** | 🔴 Critical |
| **Type** | Thread safety / race condition |

The feed thread mutates `tracker.trains` while the main loop iterates it. This can produce inconsistent passage logging or `RuntimeError: dictionary changed size during iteration`.

**Fix:** Take a locked snapshot before iterating, or add a `tracker` method that returns a safe copy:

```python
# Option A: snapshot under lock
with tracker.lock:
    snapshot = dict(tracker.trains)
for train in snapshot.values():
    ...

# Option B: tracker method
def get_trains_snapshot(self) -> dict[str, TrackedTrain]:
    with self.lock:
        return dict(self.trains)
```

### 1.2 SQLite Connections Don't Inherit `busy_timeout`

| | |
|---|---|
| **File** | `src/history.py` |
| **Severity** | 🔴 Critical |
| **Type** | Database reliability |

`_init_db` sets `busy_timeout` but subsequent `sqlite3.connect()` calls don't. Concurrent feed/API writes can fail with `"database is locked"`.

**Fix:** Centralise DB connection creation in a helper that sets timeout on every connection:

```python
def _connect(self) -> sqlite3.Connection:
    conn = sqlite3.connect(self.db_path)
    conn.execute(f"PRAGMA busy_timeout = {self.timeout_ms}")
    return conn
```

---

## 2. Security Findings

### Actionable Issues

| # | Finding | Severity | File | Recommendation |
|---|---|---|---|---|
| S1 | No rate limiting on `POST /feedback` | 🟠 High | `src/api.py` | Add nginx-level rate limiting (`limit_req_zone`) |
| S2 | `innerHTML` in upcoming-trains rendering uses unescaped fields like `arrival_scheduled` | 🟡 Medium | `static/app.js` | Audit all values passed to `innerHTML`; ensure `esc()` wraps every interpolated field |
| S3 | User-agent stored in feedback — XSS risk if rendered without escaping | 🟡 Medium | `src/api.py` | Always escape when rendering stored user-agent strings |
| S4 | No `Content-Security-Policy` headers | 🟡 Medium | `src/api.py` | Add CSP headers — requires removing inline `onclick` handlers first |
| S5 | Admin token comparison could use `hmac.compare_digest` | 🟢 Low | `src/api.py` | Low probability over HTTP for hobby project; fix if convenient |

### Good Practices Already in Place ✅

- Parameterised SQL queries throughout — no injection risk
- Docker runs as non-root
- XSS protection via `esc()` helper in JS
- Dependencies checked: no known CVEs

### Adjusted / False Positive Findings

| Finding | Original Severity | Adjusted | Reason |
|---|---|---|---|
| `.env` file with credentials on disk | 🟡 Medium | ℹ️ Info | Normal for Docker deployment if `.gitignore`d |
| CORS headers missing | 🟡 Medium | ❌ False positive | No CORS headers = browsers block cross-origin by default. The concern was backwards. |

---

## 3. Test Coverage

### Module Coverage

| Module | Coverage | Status | Notes |
|---|---|---|---|
| `models.py` | 100% | ✅ | Fully covered |
| `inferrer.py` | 95% | ✅ | |
| `tracker.py` | 93% | ✅ | |
| `history.py` | 90% | ✅ | |
| `api.py` | 87% | ✅ | Feedback endpoints untested |
| `feed.py` | 57% | ⚠️ | `on_message`, gzip decompression, JSON parsing untested |
| `rtt.py` | 0% | ❌ | Auth, rate limiting, HTTP polling, error paths all untested |
| `main.py` | 0% | ❌ | Entry point — lower priority |

### Key Gaps

| Gap | Priority | Why It Matters |
|---|---|---|
| RTT tests (token refresh, 429, timeout, malformed responses) | 🔴 Critical | RTT is a live data source; failures here silently disable features |
| `feed.on_message` tests (gzip, JSON parsing, error paths) | 🟠 High | Core data ingestion path |
| Feedback endpoint tests | 🟡 Medium | Untested POST handler with DB writes |
| Error handling paths across modules | 🟡 Medium | Happy path tested, failure modes less so |

> **Note:** `test_api.py` uses `TestClient` + real temp SQLite, which is integration-ish — the claim of "no integration tests" was overstated.

---

## 4. Code Quality

### 🟠 High

| Finding | File | Detail |
|---|---|---|
| Window merging logic duplicated | `src/inferrer.py`, `src/api.py` | DRY violation — extract shared utility function |
| Predictions endpoint too long | `src/api.py` | ~150 lines single function; break into helpers |

### 🟡 Medium

| Finding | File | Detail |
|---|---|---|
| `_handle_td` complexity | `src/feed.py` | 71 lines, high cyclomatic complexity |
| SF_MSG/SG_MSG handlers near-identical | `src/feed.py` | DRY — extract shared handler logic |
| Monkey-patching `_passage_logged` | `src/tracker.py` | Mutating dataclass at runtime; use a proper field |
| No config validation | `src/main.py` | Bad `config.yaml` silently uses defaults |
| Mixed `Optional[X]` vs `X \| None` | Various | Pick one style and enforce |
| Manual `.env` parsing | `src/main.py` | Could use `python-dotenv`; low priority per rubber duck |

### 🟢 Low / ℹ️ Info

| Finding | File | Adjusted Severity | Detail |
|---|---|---|---|
| Import inside `while` loop | `src/main.py` | ℹ️ Info | Python caches imports — no performance impact |
| No structured logging | Various | 🟢 Low | Nice-to-have, not urgent for hobby project |
| Mixed async/sync in FastAPI | `src/api.py` | 🟢 Low | Sync DB calls in async handlers; works but blocks event loop |
| No health check in Docker compose | `docker-compose.yml` | 🟢 Low | Add `healthcheck` for production readiness |

### Strengths ✅

- **Clean module separation** — models / tracker / inferrer / feed / history / rtt / api
- **Excellent config externalisation** — `config.yaml` for all tunable parameters
- **Good dataclass/enum usage** — strong domain modelling with type safety
- **Proper reconnection** — exponential backoff in `feed.py`
- **Smart UI behaviour** — visibility-change polling pause in `app.js`
- **XSS protection** — `esc()` helper in JS frontend

---

## 5. Tech Debt

| Item | Priority | Detail |
|---|---|---|
| SQLite connection-per-call without timeout | 🟠 High | Centralise with `_connect()` helper that sets `busy_timeout` on every connection |
| Window merging duplication | 🟠 High | Extract shared utility from `inferrer.py` and `api.py` |
| No config validation | 🟡 Medium | Validate `config.yaml` at startup; fail fast on bad config |
| RTT env var mismatch | 🟡 Medium | `.env.example` expects `RTT_USERNAME`/`RTT_PASSWORD` but code uses `RTT_TOKEN` — fresh installs silently disable RTT |
| Manual `.env` parsing | 🟢 Low | Replace with `python-dotenv` when convenient |
| No type stubs for `stomp.py` | ℹ️ Info | Add `py.typed` stubs or `# type: ignore` comments |

---

## 6. Rubber Duck Verdict

### False Positives Identified

| Claim | Why It's Wrong |
|---|---|
| CORS headers missing = vulnerability | Backwards — no CORS headers means browsers **block** cross-origin requests by default |
| `esc()` only escapes 3 characters | `&`, `<`, `>` is sufficient for HTML text content escaping |
| Import inside loop = performance issue | Python caches modules after first import; subsequent `import` is a dict lookup |

### Overrated Findings

| Claim | Adjusted Assessment |
|---|---|
| `.env` file on disk is a secret leak | Normal for Docker deployment if `.gitignore`d |
| `hmac.compare_digest` urgently needed | Timing attacks over HTTP on a hobby project are low probability |
| "No integration tests" | Overstated — `test_api.py` uses `TestClient` + real temp SQLite |

### Missed by Initial Audits

| Finding | Why It Matters |
|---|---|
| Thread safety bug in `main.py:97-101` | Real race condition causing potential runtime errors |
| SQLite timeout bug in `history.py` | Concurrent writes can fail silently |
| RTT env var mismatch | Fresh installs silently disable RTT data |
| Actual XSS sink analysis in `innerHTML` | Initial audit flagged `esc()` but missed real `innerHTML` sinks |

**Overall:** No catastrophic design failures. The initial audits over-weighted hygiene items (style, logging, `.env`) and under-weighted reliability bugs (thread safety, SQLite timeout). The rubber duck review corrected the balance.

---

## 7. Top 5 Priority Fixes

| # | Fix | Effort | Impact |
|---|---|---|---|
| 1 | Fix SQLite connection creation — every connection gets `busy_timeout` | Small | 🔴 Eliminates "database is locked" failures |
| 2 | Fix `tracker.trains` snapshotting/locking in main loop | Small | 🔴 Eliminates race condition crashes |
| 3 | Add RTT tests (token refresh, 429, timeout, malformed responses) | Medium | 🟠 Covers 0% → reasonable coverage on critical module |
| 4 | Add feedback endpoint tests + nginx rate limiting | Medium | 🟠 Closes spam vector + test gap |
| 5 | Fix RTT env/config mismatch + add startup config validation | Small | 🟡 Prevents silent feature disablement |

---

## 8. Recommendations by Priority

### Do Now 🔴
- Fix SQLite `_connect()` helper with `busy_timeout` on every connection
- Fix `tracker.trains` iteration with locked snapshot
- Fix RTT env var mismatch (`.env.example` vs code)

### Do Soon 🟠
- Add RTT module tests (token refresh, 429, timeout, malformed responses)
- Add feedback endpoint tests
- Add nginx rate limiting on `POST /feedback`
- Extract shared window-merging logic (DRY)
- Break up 150-line predictions endpoint
- Audit `innerHTML` sinks in `app.js` — ensure `esc()` wraps all interpolated values

### Do Later 🟡
- Add startup config validation (fail fast on bad `config.yaml`)
- Refactor `_handle_td` in `feed.py` (reduce complexity)
- Extract shared SF_MSG/SG_MSG handler
- Add `Content-Security-Policy` headers (after removing inline `onclick`)
- Add `healthcheck` to Docker compose
- Switch to `python-dotenv`
- Standardise on `X | None` style

### Don't Bother 🟢
- `hmac.compare_digest` for admin token (low risk over HTTP)
- Structured logging (nice-to-have, not impactful for hobby project)
- Enterprise patterns (connection pooling, DI containers, etc.)
- Type stubs for `stomp.py` (cosmetic)
- Fixing the "import in loop" — it's already cached by Python
