# 02 — Train Describer (TD) Messages

Train Describer messages report **train movements between track circuits** (berths). They tell you where trains are on the network in near real-time.

All TD messages are delivered via the `/topic/TD_ALL_SIG_AREA` subscription and share these common fields:

| Field | Type | Description |
|-------|------|-------------|
| `msg_type` | string | Message type: `CA`, `CB`, `CC`, or `CT` |
| `area_id` | string | Two-character TD area code (see [04-area-mapping.md](04-area-mapping.md)) |
| `time` | string | Unix epoch milliseconds when the event occurred |

---

## CA — Berth Step (train moves between berths)

The most common and most useful message. Reports a train moving from one berth to an adjacent berth.

```json
{
  "CA_MSG": {
    "msg_type": "CA",
    "area_id": "LA",
    "time": "1778140115000",
    "from": "0040",
    "to": "0038",
    "descr": "1H09"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `from` | string | 4-character berth the train is leaving |
| `to` | string | 4-character berth the train is entering |
| `descr` | string | 4-character train description (headcode). See [Headcodes](#headcodes) below |

**Interpretation:** Train `1H09` has moved from berth `0040` to berth `0038` in TD area `LA`.

### What is a berth?

A "berth" in TD terminology corresponds to a **track circuit** or **axle counter section** — a physical segment of track that can detect the presence of a train. When a train moves from one section to the next, a CA message is generated.

Berths are identified by 4-character alphanumeric codes. The format varies by area:
- Numeric with leading zeros: `0038`, `0012` (common in our area, LA)
- Letter-prefixed: `BH74`, `AR03`, `A036`, `FL30`
- Mixed: `T684`, `L062`, `ARAP`

**The berth code does NOT indicate track direction.** However, in our local area (LA), we observe a consistent convention:
- **Even-numbered berths** (0036, 0038, 0040) = **Up line** (eastbound, towards Brighton)
- **Odd-numbered berths** (0037, 0039, 0041) = **Down line** (westbound, towards Littlehampton)

> ⚠️ **Confidence: HIGH** for our local area (LA). Verified against the TD area diagram (`docs/wiki-pages/TD_Map_LA.png`) and 600+ logged train events: every train direction='up' uses even berths and A030/A032; every direction='down' uses odd berths and A027/A029/A031. It may not apply to all areas.

---

## CB — Berth Cancel (train description removed)

A train description is removed from a berth without stepping to another berth.

```json
{
  "CB_MSG": {
    "msg_type": "CB",
    "area_id": "X1",
    "time": "1778140115000",
    "from": "V751",
    "descr": "2J15"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `from` | string | 4-character berth being cancelled |
| `descr` | string | 4-character train description being removed |

**When does this happen?**
- Train reaches the end of a TD area and there is no berth to step into
- Train enters a depot, siding, or other non-tracked area
- Manual correction by the signaller
- Train is cancelled or removed from the signalling system

---

## CC — Berth Interpose (train description placed)

A train description is placed into a berth without stepping from another berth.

```json
{
  "CC_MSG": {
    "msg_type": "CC",
    "area_id": "BM",
    "time": "1778140005000",
    "to": "H987",
    "descr": "1C10"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `to` | string | 4-character berth receiving the description |
| `descr` | string | 4-character train description being placed |

**When does this happen?**
- Train enters a TD area from an adjacent area (the receiving area interposes the description)
- Train departs from a station/depot at the start of its journey
- Signaller manually interposes a description (e.g. after a system error)

**Note:** A CC in one area often corresponds to a CB in the adjacent area — the train's description is cancelled from the old area and interposed in the new one.

---

## CT — Heartbeat

Periodic message confirming the TD system for an area is alive and functioning.

```json
{
  "CT_MSG": {
    "msg_type": "CT",
    "area_id": "LA",
    "time": "1778140115000",
    "report_time": "0848"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `report_time` | string | 4-digit time in HHMM format (local time) |

**Frequency:** Approximately once per minute per area.

**Use case:** If you stop receiving CT messages for an area, the connection to that area's TD system may have failed.

---

## Headcodes

The `descr` field contains a 4-character **headcode** (also called train description). This identifies the service:

| Character | Meaning |
|-----------|---------|
| 1st | Service class: `1` = express passenger, `2` = stopping passenger, `3` = parcels, `5` = empty coaching stock, `6` = freight, `0`/`9` = light locomotive |
| 2nd | Destination/route indicator letter (A-Z) |
| 3rd–4th | Numeric sequence within the route |

Examples from our area:
- `1H09` — Express passenger, H-route (London Victoria to south coast), service 09
- `2Y13` — Stopping passenger, Y-route (local service)
- `5H91` — Empty coaching stock, H-route (positioning move)
- `1N05` — Express passenger, N-route

**Special value:** `NONE` — appears when a berth is occupied but no headcode is assigned (e.g. engineering trains, track circuit failures).

> ⚠️ **Confidence: MEDIUM** for the class digit. The digit meanings are well-documented by Network Rail. The route letter meanings are less standardised and vary by region.

---

## Berth Overlap Between Areas

A single physical berth can appear in **multiple TD areas simultaneously**. This means the same train movement may generate CA messages in more than one area.

Confirmed examples from our data:

| Berth | Areas seen in |
|-------|---------------|
| BH71 | BM, LA, ZB |
| H989 | BM, ZH |
| BH74 | BM |
| AR03 | ZH |

> ⚠️ **Confidence: HIGH.** Confirmed from raw message capture. If you're counting events, be aware of potential double-counting from overlapping areas.

---

## Train Journey Example

A westbound train (`1H09`) approaching Roundstone Level Crossing:

```
Time        Area  From → To    Notes
─────────────────────────────────────────────────
06:03:15    LA    0024 → 0020  Approaching from east
06:04:12    LA    0020 → 0018
06:05:07    LA    0018 → 0016
06:07:04    LA    0016 → 0014
06:08:10    LA    0014 → 0012  
06:08:43    LA    0012 → 0010  ← Crossing is between 0038/0039 and 0036/0037
06:12:05    LA    0010 → 0008  
06:13:16    LA    0008 → 0006  Moving away westbound
```

**Correction:** The above example uses lower-numbered berths. Our crossing is actually between berths **0036/0039** (east side) and **0038/0041** (west side). A correct crossing approach:

```
Eastbound (Up line, even berths) — train moves east, berth numbers decrease:
  0042 → 0040 → 0038 → [CROSSING] → 0036 → 0034 → 0032 → 0030

Westbound (Down line, odd berths) — train moves west, berth numbers increase:
  0031 → 0033 → 0035 → 0037 → 0039 → [CROSSING] → 0041 → A027 → A029 → A031
```

Each berth step takes approximately 45–120 seconds depending on train speed and berth length.
