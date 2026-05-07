# 03 — Signalling Function (SF) Messages

SF messages report **bit-level state changes** in signalling equipment. They are the raw digital output of the signalling system — each message tells you that a specific byte of data has changed to a new value.

SF messages are delivered via the same `/topic/TD_ALL_SIG_AREA` subscription as TD messages.

---

## Message Format

```json
{
  "SF_MSG": {
    "msg_type": "SF",
    "area_id": "LA",
    "time": "1778140115000",
    "address": "03",
    "data": "45"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `msg_type` | string | Always `"SF"` |
| `area_id` | string | Two-character TD area code |
| `time` | string | Unix epoch milliseconds |
| `address` | string | Hex address (2 characters, e.g. `"03"`, `"0A"`, `"1F"`) |
| `data` | string | Hex data byte (2 characters, e.g. `"45"`, `"FF"`, `"00"`) |

---

## Understanding the Data

### Address

The `address` field identifies **which group of 8 signals/indicators** has changed. Think of it as a register address in the signalling system's data bus.

Each TD area has its own address space — `address "03"` in area `LA` is completely unrelated to `address "03"` in area `BM`.

The number of addresses varies by area. In our observations:
- **Area LA** (our crossing): 8 addresses (`00`–`07`)
- **Area BM** (Barnham): 10 addresses (`00`–`09`)

### Data Byte

The `data` field is a single hex byte representing the **current state of all 8 bits** at that address. It is NOT a delta — it is the complete new value.

Convert to binary to see individual bit states:

```
data = "45"
binary = 01000101

Bit:  7 6 5 4 3 2 1 0
      0 1 0 0 0 1 0 1
```

**Each bit represents one signalling function** — a signal aspect, a point position, a route indicator, or other equipment state.

### What Do the Bits Mean?

The bit-to-function mapping is defined in each area's **Signalling Output Plan (SOP)**. SOPs are published on the [Open Rail Data wiki](https://wiki.openraildata.com) (some are marked "work in progress") and can be verified through the "Breaking The Code" correlation methodology — correlating CA berth steps with SF bit changes within ±5s.

Each TD area has a **capability specification** that determines which data types it carries:

| Type | Code | Description |
|------|------|-------------|
| SIG | Signal | Signal aspects (red/green/yellow) |
| RTE | Route | Route indicators (which route is set through a junction) |
| TRK | Track | Track circuit occupancy |
| PTS | Points | Point positions (normal/reverse) |
| LXG | Level crossing | Level crossing barrier state |
| LAT | Latching | Latching indicators (e.g. TRTS — Train Ready To Start) |

Not all areas carry all types. For example, area **LA carries RTE only** — no signals, track circuits, or level crossings. Area **BM carries SIG + RTE + LAT + PTS + LXG** — a full set. See [04-area-mapping.md](04-area-mapping.md) for the capability matrix.

Complete SOPs for our local areas:
- **LA (Lancing):** [06-la-sop.md](06-la-sop.md) — 34 documented route bits, 7 undocumented active bits
- **BM (Barnham):** [07-bm-sop.md](07-bm-sop.md) — 80 bits across signals, routes, TRTS, and level crossing

> ⚠️ **Confidence: HIGH** for LA and BM bit mappings. Wiki SOPs verified against observed data using correlation analysis. See [05-observations.md](05-observations.md) for methodology and evidence.

---

## Interpreting State Changes

SF messages are only sent when a value **changes**. If address `03` has data `44` and nothing changes, no message is sent. You will only see a new SF message when the value changes to something different (e.g. `45`).

To track the full state, maintain a state table:

```python
state = {}  # (area_id, address) → current data byte

def on_sf_message(area_id, address, data_hex):
    key = (area_id, address)
    old_value = state.get(key)
    new_value = int(data_hex, 16)
    state[key] = new_value

    if old_value is not None:
        changed_bits = old_value ^ new_value
        for bit in range(8):
            if changed_bits & (1 << bit):
                old_bit = (old_value >> bit) & 1
                new_bit = (new_value >> bit) & 1
                print(f"  {area_id} addr={address} bit{bit}: {old_bit} → {new_bit}")
```

### First Message Problem

When you first connect, you don't know the initial state of any address. The first SF message for each address tells you the current value, but you can't determine which bits changed because you don't have the previous value.

**Recommendation:** Discard the first message for each (area, address) pair, or wait for a complete heartbeat cycle before trusting state.

---

## SG Messages

`SG_MSG` messages use the **same format** as SF messages. They are **periodic full-state refreshes** — the signalling system sends the complete current value of every address at regular intervals, regardless of whether anything has changed.

SG messages serve the same purpose as CT heartbeats do for TD: they allow a newly-connected client to learn the full state without waiting for every address to change naturally. They also recover from any missed SF messages.

In practice, SG messages appear at a lower frequency than SF. Initial captures may not observe them if the capture window is shorter than the refresh interval.

> ⚠️ **Confidence: HIGH.** SG messages confirmed as periodic full-state refreshes through extended data capture.

---

## Volume and Frequency

SF messages are the highest-volume message type on the TD feed:

| Metric | Value |
|--------|-------|
| Messages per minute (network-wide) | ~5,200 |
| Messages per minute (area LA only) | ~16 |
| Messages per minute (area BM only) | ~39 |
| Unique addresses (area LA) | 8 |
| Unique addresses (area BM) | 10 |

Figures from a 60-second capture on 2026-05-07 08:48 UTC.

---

## Example: Tracking a Signal Change

Here's a real sequence from area LA, address `07`. This address only uses bit 0, toggling between `00` and `01`:

```
Time                 Data   Binary     Bit 0
───────────────────────────────────────────────
2026-05-07T06:26:32  0x01   00000001   ON
2026-05-07T06:29:14  0x00   00000000   OFF   (162s later)
2026-05-07T06:58:27  0x01   00000001   ON    (29 min gap)
2026-05-07T07:01:01  0x00   00000000   OFF   (154s later)
2026-05-07T07:23:30  0x01   00000001   ON    (22 min gap)
2026-05-07T07:27:50  0x00   00000000   OFF   (260s later)
```

This bit toggles ON for 2–4 minutes then OFF for 20–30 minutes. It correlates loosely with train movements through the area but its exact function is **unknown**.

---

## Example: Level Crossing Indicator

Area BM, address `09`, bit 6 is `L(YN)(DN)` — the **Yapton level crossing barriers DOWN** indicator. It correlates 100% with train arrivals at berths BH74/BH75 because Yapton crossing lowers for those trains.

```
Sequence for each train:
  1. bit6 → 1  (Yapton barriers go DOWN)
  2. [train passes through BH74 or BH75]
  3. bit6 → 0  (Yapton barriers come UP)
```

This was initially interpreted as a signal aspect (see [05-observations.md](05-observations.md), Finding 3). The complete BM SOP decode revealed it is actually a level crossing indicator. The 100% correlation with train movements is correct — it was the interpretation that was wrong.

See [07-bm-sop.md](07-bm-sop.md) for the complete BM SOP. Bit 5 at the same address (`09:5`) is `L(YN)(FAILD)` — the Yapton crossing FAILED indicator.

> ⚠️ **Confidence: HIGH.** Confirmed from wiki SOP. The naming convention `YN` = Yapton is consistent with BM area geography.

---

## LA Area Address Summary

LA contains **route indicators only** (no signals, track circuits, or level crossings). The complete SOP decode is in [06-la-sop.md](06-la-sop.md).

| Address | Bits Used | Function | Notes |
|---------|-----------|----------|-------|
| `00` | 0–7 | Routes R1–R7 + 1 unknown | Bit 0 undocumented (3 changes) |
| `01` | 0–7 | Routes R8–R15 | Fully documented |
| `02` | 0–7 | Routes R16–R28 + 1 unknown | Bit 3 undocumented (17 changes, possibly TRTS) |
| `03` | 0–7 | Routes R29–R31, RA010 + 2 unknown | Bit 6 = block section indicator (114 changes) |
| `04` | 0–7 | Routes R31b–R35 + 2 unknown | Bits 1, 7 undocumented (rarely used) |
| `05` | 0–6 | Routes RA006–RA011b | Fully documented |
| `06` | 0–1 | Route RA012 + 1 unknown | Bit 1 undocumented (5 changes) |

**Key crossing-area routes:** R27, R28, R29, R31, R32, R33, R34, R34b, R35, RA007, RA008, RA010

> ⚠️ **Confidence: HIGH.** 34 documented bits confirmed from wiki SOP, verified against observed data. 7 undocumented bits characterised from 9 days of data analysis (660 crossing events).
