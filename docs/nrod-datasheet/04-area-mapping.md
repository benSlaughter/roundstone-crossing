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

### Other Areas Observed

In a 60-second capture, **188 unique TD areas** were seen across the entire national network. We only subscribe to all areas via `TD_ALL_SIG_AREA` — there is no way to subscribe to a single area.

---

## Berth Numbering Convention (Area LA)

In area LA, berths use 4-digit numeric codes with leading zeros. The tracks are dual-line (Up and Down), and each line has its own berth sequence:

```
                EAST (towards Brighton)                    WEST (towards Littlehampton)
                ◄────────────────────────────────────────────────────────────────►

Up line     ...0031 ── 0033 ── 0035 ── 0037 ──╌╌╌╌╌╌╌── 0039 ── 0041...
(odd,                                          ╳               
eastbound)                                  CROSSING            
                                               ╳               
Down line   ...0030 ── 0032 ── 0034 ── 0036 ──╌╌╌╌╌╌╌── 0038 ── 0040 ── 0042...
(even,
westbound)
```

### Direction Convention

| Berth Numbers | Track | Direction | Railway Term |
|---------------|-------|-----------|-------------|
| Even (0036, 0038, 0040...) | Down line | Westbound (→ Littlehampton) | Down |
| Odd (0037, 0039, 0041...) | Up line | Eastbound (→ Brighton) | Up |

> ⚠️ **Confidence: HIGH.** Verified across 50+ train journeys by matching headcodes against known timetable directions.

### Train Direction from Berth Sequence

A westbound train steps through **decreasing** even berth numbers:
```
0042 → 0040 → 0038 → [CROSSING] → 0036 → 0034 → 0032 → 0030
```

An eastbound train steps through **increasing** odd berth numbers:
```
0031 → 0033 → 0035 → 0037 → [CROSSING] → 0039 → 0041
```

---

## Roundstone Level Crossing Location

The crossing sits between berths **0036/0037** (east side) and **0038/0039** (west side).

```
                        ┌─── Roundstone Level Crossing
                        │
                        ▼
    ...0035 ── 0037 ──╌╌╳╌╌── 0039 ── 0041...    Up (eastbound)
    ...0034 ── 0036 ──╌╌╳╌╌── 0038 ── 0040...    Down (westbound)
```

**Key berths for crossing prediction:**

| Berth | Meaning |
|-------|---------|
| `0040` | Westbound train 2 berths from crossing |
| `0038` | Westbound train about to cross / on crossing |
| `0036` | Westbound train has just passed crossing |
| `0035` | Eastbound train 2 berths from crossing |
| `0037` | Eastbound train about to cross / on crossing |
| `0039` | Eastbound train has just passed crossing |

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
