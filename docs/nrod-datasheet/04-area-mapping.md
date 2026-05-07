# 04 — Area Mapping and Berth Layout

## TD Area Codes

Each TD message includes a 2-character `area_id` that identifies the signalling area. The NROD area codes **do not match** the labels shown on signalling diagrams.

### Our Local Areas

| Diagram Label | NROD Area | Covers | Notes |
|---------------|-----------|--------|-------|
| LG | LA | Angmering, Goring-by-Sea, Durrington-on-Sea, Ford | Our primary area — contains Roundstone Level Crossing |
| BH | BM | Barnham junction and platforms | Junction where Littlehampton branch diverges |
| AR | ZH | Arundel / Ford area, links between BM and LA | Overlap area — some berths appear in both BM and ZH |

> ⚠️ **Confidence: HIGH** for LA/LG and BM/BH mapping (confirmed by matching berth names in data vs diagram). **MEDIUM** for ZH/AR (fewer data points).

### TD Capability Matrix

Each TD area has a specification that defines which data types its SF messages carry. This is critical for understanding what information is (and isn't) available in each area.

| Area | SIG | RTE | TRK | PTS | LXG | LAT | Notes |
|------|-----|-----|-----|-----|-----|-----|-------|
| **LA** (Lancing) | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | **Routes only** — no barrier state possible |
| **BM** (Barnham DTD) | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | Full set — includes Yapton crossing LXG |

Source: [List of Train Describers](https://wiki.openraildata.com/index.php/List_of_Train_Describers) wiki page.

**Key implication:** LA's SF data contains only route indicators. There is no signal, track circuit, or level crossing data in area LA. This is why exhaustive analysis of all 64 LA bits found no barrier correlation — the barrier state simply isn't in the data. See [06-la-sop.md](06-la-sop.md) for the complete LA SOP and [07-bm-sop.md](07-bm-sop.md) for BM.

> ⚠️ **Confidence: HIGH.** Confirmed from the Open Rail Data wiki and validated against observed data (LA bits are all routes, BM bits include signals, routes, TRTS, and level crossing indicators).

### Other Areas Observed

In a 60-second capture, **188 unique TD areas** were seen across the entire national network. We only subscribe to all areas via `TD_ALL_SIG_AREA` — there is no way to subscribe to a single area.

---

## Berth Numbering Convention (Area LA)

In area LA, berths use 4-digit numeric codes with leading zeros. The tracks are dual-line (Up and Down), and each line has its own berth sequence:

```
                EAST (towards Brighton)                    WEST (towards Littlehampton)
                ◄────────────────────────────────────────────────────────────────►

Up line     ...0030 ── 0032 ── 0034 ── 0036 ──╌╌╌╌╌╌╌── 0038 ── 0040 ── 0042...
(even,                                         ╳
eastbound)                                  CROSSING
                                               ╳
Down line   ...0031 ── 0033 ── 0035 ── 0037 ──╌╌╌╌╌╌╌── 0039 ── 0041 ── A027...
(odd,
westbound)
```

### Direction Convention

| Berth Numbers | Track | Direction | Railway Term |
|---------------|-------|-----------|-------------|
| Even (0030, 0032, 0034...) | Up line | Eastbound (→ Brighton) | Up |
| Odd (0031, 0033, 0035...) | Down line | Westbound (→ Littlehampton) | Down |

> ⚠️ **Confidence: HIGH.** Verified against the TD area diagram (`docs/wiki-pages/TD_Map_LA.png`) and 600+ logged train events with inferred directions. Note that the parity-to-direction mapping is the **opposite** of what an earlier revision of this document claimed.

### Train Direction from Berth Sequence

An eastbound train steps through **decreasing** even berth numbers:
```
0042 → 0040 → 0038 → [CROSSING] → 0036 → 0034 → 0032 → 0030
```

A westbound train steps through **increasing** odd berth numbers (continuing into A-prefixed berths past Angmering P2):
```
0031 → 0033 → 0035 → 0037 → 0039 → [CROSSING] → 0041 → A027 → A029 → A031
```

---

## Roundstone Level Crossing Location

The crossing sits between berths **0036/0039** (east side) and **0038/0041** (west side). In railway terms:
- **Eastbound:** the crossing is just past signal 38, at the western start of berth `0036`
- **Westbound:** the crossing is just past signal 39, at the eastern start of berth `0041`

```
                        ┌─── Roundstone Level Crossing
                        │
                        ▼
    ...0034 ── 0036 ──╌╌╳╌╌── 0038 ── 0040...    Up (eastbound, even)
    ...0035 ── 0037 ──╌╌╳╌╌── 0039 ── 0041...    Down (westbound, odd)
```

**Key berths for crossing prediction:**

| Berth | Meaning |
|-------|---------|
| `0042` | Eastbound train at western edge of area LA (approach) |
| `0040` | Eastbound train at Angmering P1 (strike-in) |
| `0038` | Eastbound train approaching crossing, just before signal 38 (strike-in) |
| `0036` | Eastbound train at the crossing (just past signal 38) |
| `0034` | Eastbound train has passed crossing, heading towards Goring (cleared) |
| `0033` | Westbound train at eastern edge of area LA (approach) |
| `0035` | Westbound train at Goring P2 (strike-in) |
| `0037` | Westbound train approaching crossing (strike-in) |
| `0039` | Westbound train approaching crossing, just before signal 39 (strike-in) |
| `0041` | Westbound train at the crossing (just past signal 39, also Angmering P2) |
| `A027` | Westbound train past Angmering P2, departed westward (cleared) |

---

## Barnham Junction Layout (Area BM)

Barnham is where the Littlehampton branch diverges from the main line. The berth layout around the junction:

```
Main line (from Chichester)                    Main line (towards Arundel/Ford)
    ...BH77 ── BH75 ── BH73 ──┐         ┌── H987 ── H989 ── A001...
                                ├── 119 ──┤
    ...BH72 ── BH74 ── BH76 ──┘         └── (junction throat)
                                │
                            Platforms
                          (117, 119, 120)
                                │
                         To Littlehampton
                           (branch line)
                         BH39, BH40, etc.
```

**Berth direction at Barnham:**
- **Odd berths** (BH73, BH75, BH77): Down platform line (westbound arrivals/eastbound departures)
- **Even berths** (BH72, BH74, BH76): Up platform line (eastbound arrivals/westbound departures)

> ⚠️ **Confidence: MEDIUM.** Inferred from train headcode patterns and diagram. The junction throat layout is approximate.

### Barnham Approach from East

Trains approaching Barnham from the Arundel/Ford direction follow this berth sequence:

```
H987 → H989 → A001 → AR01 → AR03 → AR05 → ARAP → BH73 → BH75 → BH77
```

Note that this sequence crosses **three TD areas**:
- `BM` for H987, H989, A001, ARAP, BH73, BH75
- `ZH` for AR01, AR03, AR05

> ⚠️ **Confidence: HIGH.** Confirmed from multiple train journeys in overnight data.

---

## Wider Area Map

```
                      EAST                                    WEST
    ◄─────────────────────────────────────────────────────────────────►

    Goring    Durrington  ANGMERING    Ford    BARNHAM     Chichester
    ─────────────────────────────────────────────────────────────────
    ...0028   0030  0032  0034  0036  ╌╌  0038  0040  0042  A036...
              (area LA)                                    (area BM)
                          CROSSING ──╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
                                                    │
                                               Littlehampton
                                               (branch via BH39/40)
```

### Area Boundaries

TD area boundaries are marked on signalling diagrams with labels like `LG:AR` or `BH:AR`. A train crossing an area boundary will:

1. Generate a **CB** (cancel) in the departing area
2. Generate a **CC** (interpose) in the arriving area
3. Continue generating **CA** (step) messages in the new area

Some berths near boundaries appear in **multiple areas** — the same physical track circuit is reported by both area's TD systems.

---

## Station CRS Codes

For reference, the station codes used by our RTT integration:

| Station | CRS Code | Area |
|---------|----------|------|
| Angmering | ANG | LA |
| Littlehampton | LIT | LA/BM |
| Ford | FOD | LA/BM |
| Barnham | BAM | BM |
| Arundel | ARU | BM/ZH |
| Goring-by-Sea | GBS | LA |
| Durrington-on-Sea | DUR | LA |
