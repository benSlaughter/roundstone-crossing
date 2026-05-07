# 05 — Observations and Experimental Findings

This document records findings from our signal analysis, starting with overnight logging on 2026-05-06/07 and extended through 9 days of continuous data capture and 12 live observation windows at the crossing.

---

## Experiment Setup

**Logger:** `experiments/signal_logger.py` — standalone STOMP listener that captures TD and SF events for selected areas and berths.

**Database:** `experiments/signal_data.db` (SQLite)

**Configuration:**
- Watch areas: `LA`, `BM`, `ZH`
- Watch berths: `BH74`, `BH75`, `AR07`, `AR05`, `AR03`, `H987`, `H989`, `ARAP`
- All TD and SF messages in the watched areas are logged
- TD messages for watched berths are logged regardless of area

**Data captured:**
- Initial overnight session: 2026-05-06 21:16 to 2026-05-07 07:51 UTC (~10.5 hours)
- Extended capture: 9 days continuous, 660 crossing events
- Live observation: 12 windows with user standing at crossing

---

## Methodology: "Breaking The Code"

From the Open Rail Data wiki article ["Decoding S-Class Data"](https://wiki.openraildata.com):

> When a train steps from berth A to berth B (CA message), it has passed signal A. The signal protecting berth A goes to RED (bit → 0).

**Correlation technique:** Match CA berth steps with SF bit changes within ±5 seconds. A bit that consistently changes state when trains pass a specific berth is likely the signal or route associated with that berth.

This methodology works perfectly for BM signals (80–100% correlation, confirming the technique) and was applied to LA to verify all documented route bits from the wiki SOP.

---

## Finding 1: Even/Odd Berth Convention

**Status: CONFIRMED**

In area LA, berth parity indicates track:
- Even numbers (0036, 0038, 0040) = Down line (westbound)
- Odd numbers (0037, 0039, 0041) = Up line (eastbound)

**Evidence:** 54 trains analysed. Every westbound train (identified by headcode) used exclusively even berths. Every eastbound train used exclusively odd berths. Zero exceptions.

---

## Finding 2: Crossing Location

**Status: CONFIRMED**

Roundstone Level Crossing is between berths **0036/0037** and **0038/0039**. Confirmed by the user marking the crossing position on the OpenTrainTimes signalling diagram.

This means:
- A westbound train enters the crossing when stepping from `0040 → 0038`
- An eastbound train enters the crossing when stepping from `0035 → 0037`
- The train has cleared the crossing when it reaches `0036` (west) or `0039` (east)

---

## Finding 3: BM addr=09 — Yapton Level Crossing (Corrected)

**Status: CONFIRMED — corrected interpretation**

> **Original interpretation (WRONG):** BM address `09`, bits 6 and 7 were interpreted as a 2-aspect signal — bit7=signal clearing, bit6=signal aspect change. The 100% correlation with train arrivals at BH74/BH75 seemed to confirm this.

**Corrected interpretation:** The complete BM SOP decode reveals:
- `09:6` = **L(YN)(DN)** — Yapton level crossing barriers DOWN indicator
- `09:5` = **L(YN)(FAILD)** — Yapton level crossing FAILED indicator
- `09:7` = *(unused)*

The naming convention `YN` = Yapton, not Roundstone. The 100% correlation with train arrivals is real — Yapton crossing lowers for those trains — but this is a **level crossing indicator**, not a signal aspect. The original "2-aspect signal sequence" interpretation was incorrect.

**Lesson:** Correlation alone can mislead. The SOP decode provided the correct interpretation that correlation-only analysis missed.

> ⚠️ **Confidence: HIGH.** Confirmed from wiki SOP. BM has LXG capability; the bit naming follows standard level crossing conventions.

---

## Finding 4: LA addr=03 bit6 — Block Section Indicator

**Status: CONFIRMED**

In area LA, address `03` bit 6 is a block section indicator. It shows:

- 114 total changes over the 9-day capture
- Stays **ON** while the section is in use — spans multiple consecutive trains during busy periods
- ON periods last 30s to 780s
- Not a per-train route; it represents section-level occupancy

**Not a barrier indicator** — the durations and patterns don't match barrier lowering/raising cycles. Not a route — it doesn't appear in the wiki SOP and its behaviour (staying set across multiple trains) is inconsistent with route indicators.

> ⚠️ **Confidence: HIGH.** 114 changes provide strong statistical evidence. Behaviour is consistent and distinct from route indicators.

---

## Finding 5: No Barrier Bit in NROD Data — CONFIRMED

**Status: CONFIRMED (definitive negative result)**

Roundstone Level Crossing barrier state is **not available** in NROD data. This is confirmed by four independent lines of evidence:

1. **TD specification:** LA's Train Describer spec has **no LXG capability**. It carries RTE (routes) only — no signals, track circuits, or level crossing data. Source: [List of Train Describers](https://wiki.openraildata.com/index.php/List_of_Train_Describers) wiki page.

2. **Exhaustive bit search:** All 64 LA bits (8 addresses × 8 bits) correlated against 49 initial crossing events. Best barrier-like correlation: **14%** (addr=05 bit1). Extended to 660 crossings over 9 days — no improvement. Maximum correlation for any bit against barrier events remains well below useful threshold.

3. **Complete SOP decode:** All 34 documented LA bits are routes. The 7 undocumented active bits are characterised as routes (5), a block section indicator (1), and a possible TRTS (1). None are barrier indicators.

4. **BM's only LXG bit is Yapton:** BM area has LXG capability and contains `L(YN)(DN)` (Yapton crossing DOWN) at addr 09 bit 6. There is no Roundstone crossing bit in BM.

**Root cause:** Roundstone is an MCB-CCTV crossing (Manually Controlled Barriers with CCTV). The barrier state exists in the local interlocking equipment but is not fed to the Train Describer system.

> ⚠️ **Confidence: HIGH.** Four independent evidence sources converge on the same conclusion. The barrier state is definitively not in NROD.

---

## Finding 6: Route-Based Barrier Inference — VALIDATED

**Status: VALIDATED (replaces earlier theoretical finding)**

> **Original finding (theoretical):** If the protecting signal is green, barriers must be down (railway interlocking safety rule).

**Updated finding:** For MCB-CCTV crossings like Roundstone, the signaller must **lower barriers and verify via CCTV before setting the route**. Therefore:

> **Route SET near crossing → barriers MUST be down**

This is stronger than signal-based inference because LA only carries route data (no signals). Routes are the only available indicator, and they have a direct causal relationship with barrier state for MCB-CCTV crossings.

### Validation: 660 crossings over 9 days

| Metric | Value |
|--------|-------|
| Total crossing events analysed | 660 |
| Crossings with at least one LA route set (±180s) | **98.8%** (652/660) |
| Route-enhanced prediction warned earlier than TD berth alone | **35%** (232/660) |
| Median extra lead time from route prediction | ~15s |
| Maximum extra lead time observed | 300–500s |

### Crossing-area routes used for inference

R27, R28, R29, R31, R32, R33, R34, R34b, R35, RA007, RA008, RA010

### Route timing characteristics

| Metric | Value |
|--------|-------|
| Route SET before train reaches signal berth | Median 300–400s |
| Route CLEAR after train passes | Median 20–140s |

> ⚠️ **Confidence: HIGH.** Validated against 660 crossings over 9 days with 98.8% coverage. The causal mechanism (signaller procedure for MCB-CCTV) is well-documented in railway operating procedures.

---

## Finding 7: Berth Overlap Across TD Areas

**Status: CONFIRMED**

A single physical berth can generate messages in multiple TD areas:

| Berth | Areas |
|-------|-------|
| BH71 | BM, LA, ZB |
| H989 | BM, ZH |

**Implication:** When counting events or tracking train positions, filter by a single area to avoid double-counting.

---

## Finding 8: Train Journey Through Barnham Approach

**Status: CONFIRMED**

The berth sequence for trains approaching Barnham from the east (Ford/Arundel direction):

```
H987 → H989 → A001 → AR01 → AR03 → AR05 → ARAP → BH73 → BH75 → BH77
```

This crosses areas BM and ZH. The `ARAP` berth is likely the bay platform approach at Barnham.

`H987` is a real berth — trains interpose there (CC message) and step to H989 (CA message). It was initially unknown but confirmed through overnight data.

---

## Finding 9: SF Message Volume Distribution

**Status: CONFIRMED**

From a 60-second network-wide capture (8,442 messages):

| Message Type | Count | % of Total |
|-------------|-------|------------|
| SF (signalling) | 5,217 | 61.8% |
| CA (berth step) | 1,906 | 22.6% |
| TRUST movements | 793 | 9.4% |
| CC (interpose) | 255 | 3.0% |
| CB (cancel) | 144 | 1.7% |
| CT (heartbeat) | 126 | 1.5% |
| RTPPM | 1 | 0.01% |

SF messages dominate the feed — they are generated every time any signal, point, or indicator changes state anywhere on the network.

---

## Finding 10: Complete LA SOP Decode

**Status: CONFIRMED**

LA SF data contains **only route indicators**. All 34 documented bits are routes. There are 7 undocumented active bits (none are barriers). The LA Train Describer specification only supports the RTE data type.

See [06-la-sop.md](06-la-sop.md) for the complete bit table with confidence levels.

---

## Finding 11: Complete BM SOP Decode

**Status: CONFIRMED**

BM has 80 bits across addresses 00–09 carrying four data types:
- **SIG** (signals): addresses 00–02, 17 signal bits
- **RTE** (routes): addresses 03–07, route indicators with some C/M qualified
- **LAT** (latching): address 08, 8 TRTS (Train Ready To Start) indicators
- **LXG** (level crossing): address 09, Yapton crossing DOWN and FAILED indicators

See [07-bm-sop.md](07-bm-sop.md) for the complete bit table.

---

## Finding 12: TD Capability Matrix

**Status: CONFIRMED**

Each TD area has a defined capability specification:

| Area | SIG | RTE | TRK | PTS | LXG |
|------|-----|-----|-----|-----|-----|
| LA (Lancing) | ❌ | ✅ | ❌ | ❌ | ❌ |
| BM (Barnham DTD) | ✅ | ✅ | ❌ | ✅ | ✅ |

This explains why LA only has route data — the TD spec simply doesn't include other types. Source: [List of Train Describers](https://wiki.openraildata.com/index.php/List_of_Train_Describers) wiki page.

---

## Finding 13: Live Observation Validation

**Status: CONFIRMED**

12 live observation windows (user standing at crossing) validated the route-based inference approach:

### Route timing vs observed barriers

- Routes SET **50–100s before** observed barrier close
- Every TD-matched train aligned within **8–43s** of human observation

### Barrier timing calibration

| Metric | Median | Range |
|--------|--------|-------|
| Barrier close → first train passes | 99s | 5–132s |
| Last train passes → barrier open | 14s | 8–15s |

### Config values (calibrated from observations)

| Parameter | Value | Basis |
|-----------|-------|-------|
| `pre_closure_secs` | 120 | Conservative — median barrier-to-train is 99s |
| `crossing_clearance` | 10 | Train clearance time |
| `post_clearance` | 5 | Last train to barrier up — median 14s minus clearance |

> ⚠️ **Confidence: HIGH.** 12 independent live observations with consistent results. Timing values cross-validated against TD data.

---

## Summary of Key Conclusions

1. **Roundstone barrier state is NOT in NROD data** — confirmed definitively (Finding 5)
2. **Route-based inference works** — 98.8% coverage, validated against 660 crossings and 12 live observations (Finding 6, 13)
3. **LA = routes only** — no signals, no barriers, by design (Finding 10, 12)
4. **BM's only crossing bit is Yapton** — not Roundstone (Finding 3, 11)
5. **Route SET ≈ barriers down** — causal relationship for MCB-CCTV crossings (Finding 6)
