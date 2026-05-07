# 07 — BM (Barnham) Signalling Output Plan

Complete SOP decode for TD area **BM** (Barnham DTD). BM covers Barnham junction and platforms, including Yapton Level Crossing.

> ⚠️ **TD Capability: SIG + RTE + LAT + PTS + LXG.** The BM Train Describer specification includes signals, routes, latching indicators, points, and level crossing data. This is confirmed by the [List of Train Describers](https://wiki.openraildata.com/index.php/List_of_Train_Describers) wiki page.

---

## Address Map

BM uses 10 addresses (`00`–`09`), encoding 80 bits total.

| Address | Type | Description |
|---------|------|-------------|
| 00–02 | SIG | Signal aspects |
| 03–07 | RTE | Route indicators (some with C/M suffixes) |
| 08 | LAT | TRTS — Train Ready To Start indicators |
| 09 | LXG + LAT | Level crossing and latching indicators |

---

## Complete Bit Table

### Address 00 — Signals

| Bit | Function | Type | Notes |
|-----|----------|------|-------|
| 0 | SBH20 | SIG | Signal BH20 |
| 1 | SBH38 | SIG | Signal BH38 |
| 2 | SBH39 | SIG | Signal BH39 |
| 3 | SBH40 | SIG | Signal BH40 |
| 4 | SBH41 | SIG | Signal BH41 |
| 5 | SBH42 | SIG | Signal BH42 |
| 6 | SBH73 | SIG | Signal BH73 |
| 7 | SBH74 | SIG | Signal BH74 |

### Address 01 — Signals

| Bit | Function | Type | Notes |
|-----|----------|------|-------|
| 0 | SBH75 | SIG | Signal BH75 |
| 1 | SBH76 | SIG | Signal BH76 |
| 2 | SBH77 | SIG | Signal BH77 |
| 3 | SBH78 | SIG | Signal BH78 |
| 4 | SBH79 | SIG | Signal BH79 |
| 5 | SBH80 | SIG | Signal BH80 |
| 6 | SBH81 | SIG | Signal BH81 |
| 7 | *(unused)* | — | |

### Address 02 — Signals

| Bit | Function | Type | Notes |
|-----|----------|------|-------|
| 0 | *(unused)* | — | |
| 1 | *(unused)* | — | |
| 2 | *(unused)* | — | |
| 3 | *(unused)* | — | |
| 4 | *(unused)* | — | |
| 5 | SBH71 | SIG | Signal BH71 |
| 6 | SBH72 | SIG | Signal BH72 |
| 7 | *(unused)* | — | |

### Address 03 — Routes

| Bit | Function | Type | Notes |
|-----|----------|------|-------|
| 0 | *(unused)* | — | |
| 1 | *(unused)* | — | |
| 2 | RBH20 | RTE | Route from BH20 |
| 3 | RBH38 | RTE | Route from BH38 |
| 4 | RBH39A | RTE | Route from BH39 (A) |
| 5 | RBH39B | RTE | Route from BH39 (B) |
| 6 | RBH40A (C/M) | RTE | Route from BH40 (A), C/M qualified |
| 7 | RBH40B (C/M) | RTE | Route from BH40 (B), C/M qualified |

### Address 04 — Routes

| Bit | Function | Type | Notes |
|-----|----------|------|-------|
| 0 | *(unused)* | — | |
| 1 | RBH41 | RTE | Route from BH41 |
| 2 | RBH75A | RTE | Route from BH75 (A) |
| 3 | RBH75B | RTE | Route from BH75 (B) |
| 4 | RBH76 | RTE | Route from BH76 |
| 5 | RBH77A | RTE | Route from BH77 (A) |
| 6 | RBH77B | RTE | Route from BH77 (B) |
| 7 | RBH78A (C/M) | RTE | Route from BH78 (A), C/M qualified |

### Address 05 — Routes

| Bit | Function | Type | Notes |
|-----|----------|------|-------|
| 0 | *(unused)* | — | |
| 1 | *(unused)* | — | |
| 2 | *(unused)* | — | |
| 3 | RBH78B (C/M) | RTE | Route from BH78 (B), C/M qualified |
| 4 | RBH78C (C/M) | RTE | Route from BH78 (C), C/M qualified |
| 5 | RBH79 | RTE | Route from BH79 |
| 6 | RBH80 | RTE | Route from BH80 |
| 7 | RBH115B/A | RTE | Route from BH115 (B/A) |

### Address 06 — Routes

| Bit | Function | Type | Notes |
|-----|----------|------|-------|
| 0 | RBH115C | RTE | Route from BH115 (C) |
| 1 | RBH117 | RTE | Route from BH117 |
| 2 | RBH119 | RTE | Route from BH119 |
| 3 | RBH120B/A | RTE | Route from BH120 (B/A) |
| 4 | RBH122B/A | RTE | Route from BH122 (B/A) |
| 5 | RBH140A | RTE | Route from BH140 (A) |
| 6 | *(unused)* | — | |
| 7 | *(unused)* | — | |

### Address 07 — Routes

| Bit | Function | Type | Notes |
|-----|----------|------|-------|
| 0 | *(unused)* | — | |
| 1 | *(unused)* | — | |
| 2 | *(unused)* | — | |
| 3 | *(unused)* | — | |
| 4 | RBH72 | RTE | Route from BH72 |
| 5 | RBH73 | RTE | Route from BH73 |
| 6 | *(unused)* | — | |
| 7 | RBH140B | RTE | Route from BH140 (B) |

### Address 08 — TRTS (Train Ready To Start)

| Bit | Function | Type | Notes |
|-----|----------|------|-------|
| 0 | TGM/GP | LAT | TRTS — platforms GM/GP |
| 1 | TEN/EL | LAT | TRTS — platforms EN/EL |
| 2 | THB | LAT | TRTS — platform HB |
| 3 | THD | LAT | TRTS — platform HD |
| 4 | TGM | LAT | TRTS — platform GM |
| 5 | TGP | LAT | TRTS — platform GP |
| 6 | TEL | LAT | TRTS — platform EL |
| 7 | TEN | LAT | TRTS — platform EN |

### Address 09 — Level Crossing + Latching

| Bit | Function | Type | Notes |
|-----|----------|------|-------|
| 0 | *(unused)* | — | |
| 1 | *(unused)* | — | |
| 2 | *(unused)* | — | |
| 3 | *(unused)* | — | |
| 4 | *(unused)* | — | |
| 5 | L(YN)(FAILD) | LXG | Yapton level crossing — FAILED indicator |
| 6 | L(YN)(DN) | LXG | Yapton level crossing — barriers DOWN indicator |
| 7 | *(unused)* | — | |

---

## Critical Finding: Yapton, Not Roundstone

Address 09 bit 6 — `L(YN)(DN)` — is the **Yapton** level crossing DOWN indicator. The naming convention `YN` = Yapton.

This bit shows 100% correlation with train arrivals at BH74/BH75 because Yapton crossing lowers for trains on those routes. The correlation is real, but the original interpretation (Finding 3 in [05-observations.md](05-observations.md)) was incorrect — this is a level crossing indicator, not a signal aspect.

**BM has no Roundstone crossing bit.** Roundstone Level Crossing is in the LA area, and the LA TD specification does not include LXG (level crossing) data. See [06-la-sop.md](06-la-sop.md) for details.

---

## Confidence

> ⚠️ **Confidence: HIGH.** Complete SOP sourced from the [Open Rail Data wiki](https://wiki.openraildata.com). Signal and route bits verified against observed data using the "Breaking The Code" correlation methodology (CA berth steps correlated with SF bit changes within ±5s). Signal bits show 80–100% correlation with expected berth activity.
