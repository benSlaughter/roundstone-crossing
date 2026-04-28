# Barrier Logger Device

A battery-powered ESP32-C3 device that sits near Roundstone Level Crossing
(Angmering, West Sussex) and logs barrier open/close events by detecting the
flashing red warning lights.

This is part of the [Roundstone Crossing Predictor](../README.md) project. The
predictor infers barrier state from Network Rail train data -- this device
provides ground truth observations for calibrating and validating those
predictions.

## Why?

The predictor works well, but "likely closed" is not the same as "definitely
closed". By logging when the barriers actually go down and come back up, we can
measure prediction accuracy, tune timing offsets, and spot edge cases (e.g.
engineering trains, failures, manual interventions) that the train data alone
cannot explain.

The device is designed to be cheap, self-contained, and low maintenance. Drop it
in position on the school run, pick it up days later, and download the logs over
WiFi from your phone.

## Features

- Detects crossing warning lights using a phototransistor with a light
  collimator (a drinking straw) to reject ambient light
- Interrupt-driven wake from deep sleep -- near-zero power draw when the
  crossing is open
- Flashing pattern detection distinguishes warning lights from car headlights
  and reflections
- DS3231 RTC maintains accurate timestamps across deep sleep cycles
- MicroSD card stores CSV logs -- weeks of data in kilobytes
- WiFi AP mode (button-activated) serves a web page on your phone to view
  status, download logs, sync the clock, and clear old data
- Runs for 10+ days on a single 18650 cell

## Bill of Materials

| Component | Example part | Approx. price |
|---|---|---|
| ESP32-C3 SuperMini | WeAct / generic | ~£4 |
| DS3231 RTC module | ZS-042 or similar | ~£2 |
| Phototransistor | TEPT5700 or similar NPN | ~£1 |
| Resistors | 10kOhm (sensor) + 2x 100kOhm (battery divider) | ~£0.20 |
| MicroSD card breakout | SPI module (Catalex etc.) | ~£2 |
| MicroSD card | Any, 1GB+ is fine | ~£3 |
| 18650 LiPo battery + holder | Flat-top unprotected OK | ~£5 |
| Tactile push button | 6mm through-hole | ~£0.20 |
| LED (optional) | 3mm, any colour, + 220ohm resistor | ~£0.10 |
| Weatherproof enclosure | Small food container / Tupperware | ~£2 |
| Light collimator | Drinking straw or pen tube | ~£0 |
| Hookup wire, perfboard | -- | ~£1 |
| **Total** | | **~£21** |

All parts are available from AliExpress, Amazon, or The Pi Hut. Nothing is
exotic -- if you have done any ESP32 or Arduino project before you probably have
most of this in a drawer already.

## How It Works

### Light Detection

UK level crossings have pairs of alternating red lights that flash at ~1Hz when
the barriers are closing or closed. The phototransistor is aimed at the lights
through a narrow tube (a drinking straw cut to ~5cm) which acts as a light
collimator, rejecting sunlight and ambient light from the side.

When a light flash hits the sensor, the voltage on GPIO2 rises above the
threshold and triggers a GPIO interrupt, waking the ESP32-C3 from deep sleep.

### Flashing Pattern Detection

Not every light pulse means the crossing is active. Car headlights, torches, and
reflections can all trigger the sensor. The firmware uses a simple pattern
detector:

- **CLOSED**: 3 or more pulses detected within a 4-second window. The
  alternating wig-wag pattern at ~1Hz reliably produces this.
- **OPEN**: No pulses for 5 consecutive seconds after a CLOSED state. This
  allows for brief gaps in detection without false OPEN events.

Single isolated pulses (headlights, reflections) are ignored.

### Power Management

The ESP32-C3 SuperMini draws roughly 5 microamps in deep sleep with the RTC
running. When a GPIO interrupt fires, the chip wakes, runs the detection
algorithm, and if a state change is confirmed, reads the RTC timestamp and
writes a line to the SD card. The whole active cycle takes under a second at
~20mA.

WiFi is only enabled when you press the button, and auto-disables after 5
minutes to avoid draining the battery.

### Power Budget Estimate

| Mode | Current draw | Duty |
|---|---|---|
| Deep sleep | ~5 uA | >99% of the time |
| Active (logging) | ~20 mA | <1 second per event |
| WiFi AP mode | ~120 mA | Up to 5 min, on demand |

With an 18650 cell (3000 mAh typical):

- Pure standby: ~25 days
- With typical crossing activity (~20 closures/day): ~14 days
- Accounting for one WiFi session per day: ~10 days

In practice, expect to swap or recharge the battery every week or two.

### WiFi AP Mode

Press the tactile button to activate WiFi. The device creates a hotspot:

- **SSID**: `RXLogger`
- **Password**: `roundstone`
- **URL**: `http://192.168.4.1`

The web page lets you:

- View device status (battery voltage, last event, total events, boot count)
- Download CSV log file
- Live debug/calibration view for aiming the sensor
- Sync the RTC to your phone's clock (automatic on page load)
- Clear old log data

WiFi automatically deactivates after 5 minutes of inactivity to conserve power.

## Data Format

Logs are stored on the MicroSD card as a single CSV file:

```
/barrier_log.csv
```

Each line contains a timestamp and state:

```csv
2026-04-29T08:23:15,BOOT
2026-04-29T08:23:15,CLOSED
2026-04-29T08:26:42,OPEN
2026-04-29T08:45:03,CLOSED
2026-04-29T08:47:31,OPEN
```

- `timestamp` is ISO 8601 UTC, sourced from the DS3231 RTC.
- `state` is one of: `BOOT` (device powered on), `CLOSED` (barriers down),
  `OPEN` (barriers up), `CLEARED` (log was cleared via web UI).

The file grows by append only. A full day of typical crossing activity adds
roughly 1-2 KB.

## Deployment

### Positioning

1. Find a spot on the **public footpath or pavement** with clear line of sight
   to the crossing warning lights. The B2140 pavement on either side of the
   crossing works well.
2. Place the device on the ground, on a wall, or leaning against a post.
   **Do not attach it to any Network Rail infrastructure.**
3. Aim the sensor tube directly at one of the red warning lights. The tube
   should be roughly level and pointing straight at the light from a distance of
   5-30 metres.
4. Check the alignment: activate WiFi AP mode and watch the status page while a
   train passes. You should see pulses being detected.

### Retrieval

1. Walk past the device on your next trip.
2. Press the button, connect to the WiFi hotspot, and download the CSV files.
3. Optionally sync the clock and clear old logs.
4. Take the device home for charging, or leave it in place.

## Uploading Data

Downloaded CSV files can be uploaded to the predictor application for
calibration analysis. A future `/calibration/upload` API endpoint will accept
these files and compare observed barrier times against predicted times.

For now, place downloaded CSVs in the `data/ground_truth/` directory of the
main project for manual analysis.

## Safety

**WARNING: This device must be completely freestanding. It must not be attached
to, placed on, or in contact with any Network Rail infrastructure, including
barriers, posts, fencing, signal equipment, cabinets, or track-side furniture.**

- Place the device on public land only (footpath, pavement, grass verge).
- Do not enter the railway corridor or any area marked with Network Rail
  signage.
- Do not obstruct the footpath, road, or crossing surface.
- The device is passive and does not transmit while logging. WiFi is only active
  for a few minutes when you choose to enable it.
- If anyone asks what it is, it is a hobby electronics project that watches the
  flashing lights. It does not interact with railway systems in any way.

## Further Documentation

- [Wiring schematic and pin mapping](docs/schematic.md)
- [Assembly instructions](docs/assembly.md)
