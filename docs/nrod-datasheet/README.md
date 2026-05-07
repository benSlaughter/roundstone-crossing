# NROD Data Reference

Network Rail Open Data (NROD) datasheet for the Roundstone Crossing project. This documentation covers the real-time data feeds we consume, how to interpret them, and what we've discovered through analysis.

## Contents

| Document | Description |
|----------|-------------|
| [01-connection.md](01-connection.md) | NROD account setup, STOMP connection, and topic subscriptions |
| [02-td-messages.md](02-td-messages.md) | Train Describer (TD) messages — berth steps, cancels, interposes |
| [03-sf-messages.md](03-sf-messages.md) | Signalling Function (SF) messages — bit-level signalling state |
| [04-area-mapping.md](04-area-mapping.md) | TD area codes, berth numbering, and our local area map |
| [05-observations.md](05-observations.md) | Findings from our signal logging experiments |
| [06-la-sop.md](06-la-sop.md) | LA (Lancing) Signalling Output Plan — complete bit decode |
| [07-bm-sop.md](07-bm-sop.md) | BM (Barnham) Signalling Output Plan — complete bit decode |

## Quick Reference

**What is NROD?**
Network Rail's real-time data platform providing train movements, signalling state, and schedule information via STOMP message queues.

**What data do we use?**
- **TD (Train Describer)** — tells us where trains are (which berth/track circuit they occupy)
- **SF (Signalling Function)** — tells us the state of signals, points, and other signalling equipment

**Key limitation:** NROD allows only **one concurrent connection per account**. A second connection will disconnect the first.
