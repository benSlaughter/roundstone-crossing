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

This is the key challenge. **The bit-to-function mapping is not published by Network Rail.** The mappings must be discovered through observation and correlation.

What we know in general:
- **Signal aspects** (red/green/yellow) are encoded in SF bits
- **Route indicators** (which route is set through a junction) are encoded in SF bits
- **Point positions** (normal/reverse) may be encoded in SF bits

What we do NOT know:
- The specific mapping of which bit = which signal/function for any given area
- Whether **level crossing barrier state** is encoded in SF data (our analysis suggests it may not be — see [05-observations.md](05-observations.md))

> ⚠️ **Confidence: LOW** for bit-level interpretation. The general categories are well-understood, but specific mappings are unconfirmed.

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

There is also an `SG_MSG` type mentioned in NROD documentation. In our 60-second capture of 8,442 messages, **no SG messages were observed**.

SG messages are believed to use the same format as SF but with a different refresh/update mechanism. They may represent the same data delivered on a different schedule.

> ⚠️ **Confidence: LOW.** We have not captured any SG messages. Their existence is based on documentation references only.

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

## Example: High-Activity Address

Area BM, address `09`, bits 6 and 7 show a clear signalling sequence that correlates 100% with train arrivals at berths BH74/BH75:

```
Sequence for each train:
  1. bit7 → 0  (signal cleared / route set)
  2. bit6 → 1  (signal aspect changes)
  3. [train passes through BH74 or BH75]
  4. bit6 → 0  (signal returns to danger)
  5. bit7 → 1  (route released)
```

This is a textbook **2-aspect signal sequence** — the signal clears for the train, then returns to danger after it passes. The ~6 second gap between bit6→0 and bit7→1 likely represents the signal replacement and route release sequence.

> ⚠️ **Confidence: HIGH** that this represents a signal aspect. The 100% correlation with 88 train movements and the consistent timing pattern are strong evidence. The specific signal identity (which physical signal on the ground) is **unknown**.

---

## LA Area Address Summary

From overnight capture (2026-05-06 21:16 to 2026-05-07 07:51 UTC):

| Address | Changes | Unique Values | Activity Level | Likely Function |
|---------|---------|---------------|----------------|-----------------|
| `00` | 6 | `00`, `01`, `08`, `20` | Very low | Unknown — different bits used each time |
| `01` | 4 | `40`, `C0` | Very low | Unknown — bits 6 and 7 only |
| `02` | 216 | 18 values | High | Signal aspects (multiple signals) |
| `03` | 237 | 38 values | Highest | Signal aspects and/or route indicators |
| `04` | 219 | 25 values | High | Signal aspects and/or route indicators |
| `05` | 188 | 26 values | High | Signal aspects and/or route indicators |
| `06` | 79 | `00`, `01`, `02`, `20`, `40`, `80` | Medium | Route indicators (single bits toggle) |
| `07` | 22 | `00`, `01` | Low | Unknown — bit 0 only, loose train correlation |

> ⚠️ **Confidence: LOW** for the "Likely Function" column. These are inferences from statistical patterns only.
