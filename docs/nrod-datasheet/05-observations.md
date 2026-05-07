# 05 — Observations and Experimental Findings

This document records findings from our signal logging experiments, run overnight on 2026-05-06/07 and continuing into daytime on 2026-05-07.

---

## Experiment Setup

**Logger:** `experiments/signal_logger.py` — standalone STOMP listener that captures TD and SF events for selected areas and berths.

**Database:** `experiments/signal_data.db` (SQLite)

**Configuration:**
- Watch areas: `LA`, `BM`, `ZH`
- Watch berths: `BH74`, `BH75`, `AR07`, `AR05`, `AR03`, `H987`, `H989`, `ARAP`
- All TD and SF messages in the watched areas are logged
- TD messages for watched berths are logged regardless of area

**Data captured (overnight session):**
- Period: 2026-05-06 21:16 to 2026-05-07 07:51 UTC (~10.5 hours)
- TD events: 4,310
- SF events: 3,304
- Trains through crossing zone: 49 (24 westbound, 25 eastbound)

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

## Finding 3: BM addr=09 bits 6&7 — Signal Aspect

**Status: HIGH CONFIDENCE**

In area BM (Barnham), SF address `09`, bits 6 and 7 show a clear signalling sequence with **100% correlation** to train arrivals at berths BH74/BH75 (88 out of 88 arrivals).

**Pattern:**
```
1. bit7 goes 0    (~30s before train arrives at BH74/BH75)
2. bit6 goes 1    (~5-15s after bit7 change)
3. Train occupies BH74 or BH75
4. bit6 goes 0    (~90-110s after train arrival)
5. bit7 goes 1    (~6s after bit6 change)
```

**Interpretation:** This is almost certainly a **signal aspect**. The sequence matches a signal clearing (bit7=0, bit6=1), train passing, then signal returning to danger (bit6=0, bit7=1). The two bits likely represent a 2-aspect or multi-aspect signal where:
- bit7=1, bit6=0 → Signal at danger (red)
- bit7=0, bit6=1 → Signal cleared (green or yellow)

---

## Finding 4: LA addr=03 bit6 — Block Section Indicator

**Status: MEDIUM CONFIDENCE**

In area LA, address `03` bit 6 is the most active bit that correlates with train movements through the crossing zone. It shows:

- 35 ON/OFF cycles during the overnight period
- During daytime (04:00+ UTC), almost every ON period contains train movements
- ON periods last 30s to 780s (many contain multiple trains)
- Several overnight ON periods have no associated trains (possible maintenance or distant traffic)

**Interpretation:** This is likely a **block section occupancy** or **route indicator** rather than a per-train signal. It stays set while the section is in use, which may span several consecutive trains during busy periods.

**NOT a barrier indicator** — the durations and patterns don't match barrier lowering/raising cycles.

---

## Finding 5: No Clear Barrier Bit in LA SF Data

**Status: HIGH CONFIDENCE (negative result)**

After correlating all 64 LA SF bits (8 addresses × 8 bits) against 49 crossing events, **no bit shows a barrier-like pattern** (lowering before train, raising after).

Best correlation for a barrier-like ON/OFF pattern: **14%** (addr=05 bit1, 7 out of 49 crossings). This is too low to represent a barrier.

**Possible explanations:**
1. Barrier state is controlled by a dedicated crossing controller that doesn't report via the TD signalling data bus
2. Barrier state may be in a TD area we're not monitoring
3. Barrier state may use SG messages (which we haven't observed)
4. The crossing may be an Automatic Half Barrier (AHB) or similar type where barrier state isn't reported digitally in the same way

---

## Finding 6: Signal State Can Infer Barrier State

**Status: THEORETICAL — needs validation**

Key insight from railway safety interlocking:

> A signal protecting a level crossing **cannot** clear to proceed (green) unless the crossing barriers are proved down and locked.

Therefore:
- If the protecting signal is green → **barriers are definitely down**
- If the protecting signal is red → barriers **may** be up or down (unknown)

This means that if we can identify the SF bit for the signal immediately before the crossing, we can infer barrier-down state (but not barrier-up state) from the SF data.

**Current status:** We have not conclusively identified which SF bit represents the protecting signal for Roundstone Level Crossing. The best candidates (addr=03 bit6, addr=05 bit1) correlate with train movements but don't show the tight per-train ON/OFF pattern expected of a protecting signal.

**Possible explanation:** The protecting signal may be further from the crossing than expected, or the signal may be encoded as a combination of bits (multi-aspect signals use 2+ bits for different aspects).

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

## Next Steps

1. **Daytime data capture** — the logger is running to collect data during peak service hours when both tracks are busy. This should give better correlation opportunities for the crossing protecting signal.

2. **Identify protecting signal** — use daytime data with more frequent trains to narrow down which SF bit represents the signal immediately before the crossing.

3. **SG message investigation** — we have not observed any SG messages. Further captures at different times may reveal them.

4. **Multi-bit signal analysis** — real signals may use 2–4 bits to encode aspect (red, yellow, double-yellow, green). Try correlating groups of bits rather than individual ones.

5. **Cross-reference with physical observation** — time a barrier lowering while watching the SF data in real-time to identify the exact bit(s).
