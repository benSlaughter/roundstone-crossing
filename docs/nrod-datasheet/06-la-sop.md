# 06 — LA (Lancing) Signalling Output Plan

Complete SOP decode for TD area **LA**. LA covers the Angmering / Goring-by-Sea / Durrington-on-Sea / Ford corridor, including Roundstone Level Crossing.

> ⚠️ **TD Capability: RTE only.** The LA Train Describer specification includes **route indicators only** — no signals (SIG), track circuits (TRK), points (PTS), or level crossing (LXG) data. This is confirmed by the [List of Train Describers](https://wiki.openraildata.com/index.php/List_of_Train_Describers) wiki page.

> The LA SOP on the wiki is marked **"work in progress"**. The documented bits below are from the wiki; undocumented bits were discovered through data analysis.

---

## Address Map

LA uses 7 active addresses (`00`–`06`). Address `07` has a single bit with loose train correlation — likely an infrastructure indicator outside the SOP.

**Total bits:** 41 active (34 documented, 7 undocumented)
**All documented bits are Routes (RTE).** No signals, no track circuits, no level crossings.

---

## Complete Bit Table

### Address 00

| Bit | Function | Type | Confidence | Notes |
|-----|----------|------|------------|-------|
| 0 | *Unknown* | — | UNKNOWN | 3 total changes; too few to analyse |
| 1 | R1 | RTE | CONFIRMED | Wiki SOP |
| 2 | R2 | RTE | CONFIRMED | Wiki SOP |
| 3 | R3 | RTE | CONFIRMED | Wiki SOP |
| 4 | R4 | RTE | CONFIRMED | Wiki SOP |
| 5 | R5 | RTE | CONFIRMED | Wiki SOP |
| 6 | R6 | RTE | CONFIRMED | Wiki SOP |
| 7 | R7 | RTE | CONFIRMED | Wiki SOP |

### Address 01

| Bit | Function | Type | Confidence | Notes |
|-----|----------|------|------------|-------|
| 0 | R8 | RTE | CONFIRMED | Wiki SOP |
| 1 | R9 | RTE | CONFIRMED | Wiki SOP |
| 2 | R10 | RTE | CONFIRMED | Wiki SOP |
| 3 | R11 | RTE | CONFIRMED | Wiki SOP |
| 4 | R12 | RTE | CONFIRMED | Wiki SOP |
| 5 | R13 | RTE | CONFIRMED | Wiki SOP |
| 6 | R14 | RTE | CONFIRMED | Wiki SOP |
| 7 | R15 | RTE | CONFIRMED | Wiki SOP |

### Address 02

| Bit | Function | Type | Confidence | Notes |
|-----|----------|------|------------|-------|
| 0 | R16 | RTE | CONFIRMED | Wiki SOP |
| 1 | R17 | RTE | CONFIRMED | Wiki SOP |
| 2 | R25 | RTE | CONFIRMED | Wiki SOP |
| 3 | *Unknown* | — | PROBABLE | 17 changes; momentary button near berth 0003 (7/10 changes correlate), possibly TRTS |
| 4 | R26a | RTE | CONFIRMED | Wiki SOP |
| 5 | R26 | RTE | CONFIRMED | Wiki SOP |
| 6 | R27 | RTE | CONFIRMED | Wiki SOP — crossing-area route |
| 7 | R28 | RTE | CONFIRMED | Wiki SOP — crossing-area route |

### Address 03

| Bit | Function | Type | Confidence | Notes |
|-----|----------|------|------------|-------|
| 0 | R29 | RTE | CONFIRMED | Wiki SOP — crossing-area route |
| 1 | R30a | RTE | CONFIRMED | Wiki SOP |
| 2 | R30b | RTE | CONFIRMED | Wiki SOP |
| 3 | *Unknown* | — | PROBABLE | 5 changes; likely a rarely-used route |
| 4 | RA010 | RTE | CONFIRMED | Wiki SOP — crossing-area route |
| 5 | RA010b | RTE | CONFIRMED | Wiki SOP |
| 6 | *Block section indicator* | — | HIGH | 114 changes; stays ON while section in use, spans multiple trains |
| 7 | R31 | RTE | CONFIRMED | Wiki SOP — crossing-area route |

### Address 04

| Bit | Function | Type | Confidence | Notes |
|-----|----------|------|------------|-------|
| 0 | R31b | RTE | CONFIRMED | Wiki SOP |
| 1 | *Unknown* | — | PROBABLE | 5 changes; likely a rarely-used route |
| 2 | R32 | RTE | CONFIRMED | Wiki SOP — crossing-area route |
| 3 | R33 | RTE | CONFIRMED | Wiki SOP — crossing-area route |
| 4 | R34 | RTE | CONFIRMED | Wiki SOP — crossing-area route |
| 5 | R34b | RTE | CONFIRMED | Wiki SOP — crossing-area route |
| 6 | R35 | RTE | CONFIRMED | Wiki SOP — crossing-area route |
| 7 | *Unknown* | — | PROBABLE | 9 changes; near crossing, likely a rarely-used route |

### Address 05

| Bit | Function | Type | Confidence | Notes |
|-----|----------|------|------------|-------|
| 0 | RA006 | RTE | CONFIRMED | Wiki SOP |
| 1 | RA007 | RTE | CONFIRMED | Wiki SOP — crossing-area route |
| 2 | RA008 | RTE | CONFIRMED | Wiki SOP — crossing-area route |
| 3 | RA009a | RTE | CONFIRMED | Wiki SOP |
| 4 | RA009b | RTE | CONFIRMED | Wiki SOP |
| 5 | RA011a | RTE | CONFIRMED | Wiki SOP |
| 6 | RA011b | RTE | CONFIRMED | Wiki SOP |

### Address 06

| Bit | Function | Type | Confidence | Notes |
|-----|----------|------|------------|-------|
| 0 | RA012 | RTE | CONFIRMED | Wiki SOP |
| 1 | *Unknown* | — | PROBABLE | 5 changes; likely a rarely-used route |

---

## Crossing-Area Routes

The following routes are relevant to Roundstone Level Crossing prediction (they SET when the signaller clears a route through or near the crossing):

**R27, R28, R29, R31, R32, R33, R34, R34b, R35, RA007, RA008, RA010**

### Route Timing (from 660 crossings over 9 days)

| Metric | Value |
|--------|-------|
| Route SET before train reaches signal berth | Median 300–400s |
| Route CLEAR after train passes | Median 20–140s |
| Crossings with at least one LA route set (±180s) | 98.8% (652/660) |

---

## Confidence Key

| Level | Meaning |
|-------|---------|
| **CONFIRMED** | Documented in wiki SOP and verified against observed data |
| **HIGH** | Strong data evidence (50+ changes, clear pattern, consistent behaviour) |
| **PROBABLE** | Some data evidence; behaviour consistent with a route or indicator but too few changes to be certain |
| **UNKNOWN** | Too few changes (≤3) to draw any conclusion |

---

## Key Takeaway

LA contains **only route data**. There are no signal aspects, no track circuits, no point positions, and — critically — **no level crossing barrier state**. This is not an omission in the SOP; it is a fundamental limitation of the LA Train Describer specification, which only supports the RTE data type.

For barrier state inference, see [05-observations.md](05-observations.md) — route SET events can be used as an indirect indicator that barriers must be down.
