# Wiring Schematic

Circuit diagram and pin mapping for the Barrier Logger device. All connections
use 3.3V logic. The ESP32-C3 SuperMini board provides a 3.3V regulator and can
be powered via USB-C or directly from a battery through the 5V/VIN pin.

## Circuit Diagram

```
                         ESP32-C3 SuperMini
                        +------------------+
                        |                  |
          Battery+ o----|  5V/VIN    3V3   |----o 3.3V rail
                        |                  |
          Battery- o----|  GND       GND   |----o GND rail
                        |                  |
                        |           GPIO2  |----+----o Phototransistor emitter
                        |                  |    |
                        |                  |    R1 10k
                        |                  |    |
                        |                  |    +----o GND
                        |                  |
                        |           GPIO3  |----+----o Button
                        |                  |    |
                        |                  |    +----o GND
                        |                  |
                        |           GPIO8  |----o RTC SDA
                        |           GPIO9  |----o RTC SCL
                        |                  |
                        |           GPIO4  |----o SD CLK
                        |           GPIO5  |----o SD MISO
                        |           GPIO6  |----o SD MOSI
                        |           GPIO7  |----o SD CS
                        |                  |
                        +------------------+


  Phototransistor                    DS3231 RTC Module
  (e.g. TEPT5700)                   +-----------+
                                    |           |
      3.3V ---o Collector           |  VCC  o---|--- 3.3V
               |                    |  GND  o---|--- GND
              [PT]                  |  SDA  o---|--- GPIO8
               |                    |  SCL  o---|--- GPIO9
      Emitter--+--- GPIO2          |           |
               |                    +-----------+
              [R1]  10kOhm
               |                    MicroSD Breakout
              GND                   +-----------+
                                    |           |
                                    |  VCC  o---|--- 3.3V
      Button                        |  GND  o---|--- GND
      +----+                        |  MOSI o---|--- GPIO6
      |    |                        |  MISO o---|--- GPIO5
  GPIO3 ---+--- GND                 |  CLK  o---|--- GPIO4
      (internal pullup)             |  CS   o---|--- GPIO7
                                    |           |
                                    +-----------+

  18650 Battery + Holder
  +---------+
  | + | - |
  +---|---|-+
      |   |
     VIN  GND
      |
      +---[R2 100k]---+--- GPIO0  (battery ADC)
                       |
                      [R3 100k]
                       |
                      GND

  Status LED (optional)
  GPIO10 ---[220ohm]---[LED]--- GND
```

## Pin Mapping

| ESP32-C3 Pin | Function | Connected to | Notes |
|---|---|---|---|
| 5V / VIN | Power in | 18650 battery + | Via battery holder; board regulator provides 3.3V |
| GND | Ground | Battery -, all module GNDs | Common ground rail |
| GPIO2 | Analog/digital in | Phototransistor emitter + 10k pulldown | Light detection, used as wake interrupt source |
| GPIO3 | Digital in (pullup) | Tactile button to GND | WiFi AP mode trigger; internal pullup enabled in firmware |
| GPIO4 | SPI CLK | MicroSD CLK | SD card clock |
| GPIO5 | SPI MISO | MicroSD MISO (DO) | SD card data out |
| GPIO6 | SPI MOSI | MicroSD MOSI (DI) | SD card data in |
| GPIO7 | SPI CS | MicroSD CS | SD card chip select |
| GPIO8 | I2C SDA | DS3231 SDA | RTC data line |
| GPIO9 | I2C SCL | DS3231 SCL | RTC clock line |
| GPIO0 | ADC input | Battery voltage divider midpoint | 2x 100kOhm divider halves battery voltage |
| GPIO10 | Digital out | Status LED (via 220ohm) | Optional; quick blink on wake, rapid blink = low battery |

## Connection Details

### Phototransistor Circuit

The phototransistor is wired in common-collector configuration with a 10kOhm
pull-down resistor to ground:

```
  3.3V ----[Collector]
                |
              (PT)    Phototransistor
                |
           [Emitter]---- GPIO2
                |
              [10k]   Pull-down resistor
                |
               GND
```

When light hits the phototransistor, current flows from collector to emitter,
pulling GPIO2 high. In darkness, the pull-down resistor holds GPIO2 low.

The GPIO2 pin is configured as a wake source for deep sleep. Any rising edge
(light detected) brings the ESP32-C3 out of deep sleep to run the detection
algorithm.

**Sensitivity tuning**: If the sensor is too sensitive (false triggers from
ambient light), increase the resistor to 22kOhm or 47kOhm. If it is not
sensitive enough (misses flashes), decrease to 4.7kOhm. The 10kOhm value works
well at 5-20 metres from the warning lights.

### DS3231 RTC Module

Standard I2C connection on GPIO8 (SDA) and GPIO9 (SCL). Most DS3231 breakout
boards (e.g. ZS-042) include pull-up resistors on the I2C lines, so no external
pull-ups are needed.

The DS3231 has a backup battery (CR2032) on the module that maintains
timekeeping if the main power is removed. This means the clock survives battery
changes.

**Important**: If using the common ZS-042 module, check whether it has a
charging circuit for the backup battery. If the module has a 200 Ohm resistor
and diode near the CR2032 holder, it is trying to charge a non-rechargeable
coin cell. Either remove the 200 Ohm resistor or replace the CR2032 with an
LIR2032 rechargeable cell.

### MicroSD Card Module

Standard SPI connection. Most cheap MicroSD breakout modules work at 3.3V. If
your module has a voltage regulator and level shifter (the ones labelled "5V"),
they will still work fine on 3.3V -- just slightly outside their designed range
but perfectly functional.

The SD card is only powered up and accessed during writes (a few milliseconds
per event) and during WiFi AP mode. The ESP32-C3 SPI peripheral is not active
during deep sleep.

Format the MicroSD card as FAT32 before first use.

### Button

A simple normally-open tactile switch between GPIO3 and GND. The firmware
enables the internal pull-up resistor on GPIO3, so the pin reads HIGH when the
button is not pressed and LOW when pressed.

The button is used to enter WiFi AP mode. It is also configured as a secondary
wake source so you can wake the device from deep sleep to activate WiFi.

### Battery

The 18650 cell connects to the VIN (5V) and GND pins on the ESP32-C3 SuperMini
board. The board has an onboard regulator that accepts input from ~3.0V to 5.5V,
so a single 18650 cell (3.0V-4.2V) works directly.

**Do not connect the battery to the 3.3V pin** -- that pin is the regulated
output and connecting a higher voltage there will damage the board.

### Battery Voltage Monitoring

A voltage divider (2x 100kOhm) on the battery input lets the firmware read
battery voltage via the ADC on GPIO0:

```
  Battery+ (VIN) ---[R2 100k]---+--- GPIO0 (ADC)
                                |
                               [R3 100k]
                                |
                               GND
```

This halves the battery voltage so 4.2V reads as ~2.1V, safely within the
ESP32-C3 ADC range (0-3.3V). The 100kOhm resistors draw negligible current
(~20 microamps at 4.2V).

The firmware reads the ADC, multiplies by the divider ratio, and shows the
result on the web status page and debug page. Below 3.3V it shows a LOW
warning; below 3.0V the device logs a warning and enters deep sleep to protect
the cell.

### Status LED

An optional LED on GPIO10 provides visual feedback without needing WiFi:

```
  GPIO10 ---[220ohm]---[LED]--- GND
```

- **Double blink on wake** -- confirms the device is alive and responding to
  light detection
- **Rapid blink (10x)** -- critical low battery, device is shutting down
- **Steady on** -- WiFi AP mode is active

If you skip the LED, the device works fine -- you just lose the visual
indicator.

If you want USB charging while deployed, the SuperMini's USB-C port can be used,
but in practice you will just swap the battery or take the device home to
charge.

## Light Collimator

The phototransistor needs to be shielded from ambient light so it only responds
to the crossing warning lights. A simple collimator does this:

```
  Warning lights                           Phototransistor
  (5-30m away)                             inside enclosure

      (*)  ========================================  [PT]
           <---------- straw or tube ----------->
           ~5cm long, ~5mm diameter
```

- Cut a drinking straw or narrow tube to about 5cm long.
- Thread the phototransistor into one end so the sensor window faces into the
  tube. A small piece of heat-shrink or tape secures it.
- The tube passes through a hole drilled in the enclosure wall.
- Aim the open end of the tube at one of the red warning lights.

The narrow tube limits the field of view to roughly 6 degrees, which is wide
enough to catch the lights from 10-20 metres away but narrow enough to reject
the sun, street lights, and headlights that are not directly in line.

You can paint the inside of the tube matte black (a marker pen works) to reduce
internal reflections.

## Weatherproofing

The device will be outdoors for days at a time in West Sussex weather, which
means rain is guaranteed.

- Use a clip-lid food container or similar as the enclosure. It does not need
  to be fully waterproof -- just keep the rain off the electronics.
- Drill a hole for the sensor tube. Seal around it with hot glue or silicone.
- Drill a small hole or leave a gap for the button, or use a waterproof button
  that mounts through the enclosure wall.
- Put a small bag of silica gel inside to absorb condensation.
- The SD card slot and USB port should be inside the enclosure and do not need
  external access during deployment.
- Point the sensor tube slightly downward (5 degrees or so) so rain runs off
  rather than pooling inside the tube.
