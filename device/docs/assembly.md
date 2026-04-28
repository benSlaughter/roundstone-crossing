# Assembly Instructions

Step-by-step guide to building the Barrier Logger device. Read through the full
[wiring schematic](schematic.md) before starting so you understand where
everything connects.

**Time required**: About 1-2 hours if you are comfortable with soldering. Longer
on a breadboard prototype.

**Tools needed**: Soldering iron (for perfboard) or breadboard, wire strippers,
small screwdriver, drill with 5-6mm bit (for enclosure), hot glue gun or
silicone sealant.

## 1. Prepare the Board

If using a **breadboard** for prototyping:
- Use a half-size breadboard. The ESP32-C3 SuperMini is small enough to leave
  plenty of room for the other components.
- Run a 3.3V power rail and a GND rail along the edges.

If using **perfboard** for a permanent build:
- Cut a piece of perfboard to fit inside your enclosure with a few millimetres
  of clearance on each side.
- Plan component placement before soldering. The ESP32-C3 goes in the centre,
  with the SD card module and RTC module on either side.

## 2. Mount the ESP32-C3 SuperMini

- Place the ESP32-C3 SuperMini on the board with the USB-C port facing outward
  (toward the edge of the enclosure, for charging access if needed).
- If using a breadboard, push the pin headers into the breadboard. The SuperMini
  straddles the centre channel.
- If using perfboard, solder pin headers to the SuperMini first, then solder
  those into the perfboard.
- Connect the **3V3** pin to your 3.3V power rail.
- Connect a **GND** pin to your ground rail.

## 3. Wire the Phototransistor Circuit

This is the light sensor that detects the crossing warning lights.

1. Identify the phototransistor leads. For the TEPT5700 and most similar parts,
   the shorter lead is the emitter and the longer lead is the collector. Check
   your datasheet if unsure.
2. Connect the **collector** (long lead) to the **3.3V rail**.
3. Connect the **emitter** (short lead) to **GPIO2** on the ESP32-C3.
4. Solder the **10kOhm resistor** between the emitter/GPIO2 junction and
   **GND**. This is the pull-down resistor that holds the pin low when no light
   is detected.

```
  3.3V rail ---[long lead]----(PT)----[short lead]---+--- GPIO2
                                                     |
                                                   [10k]
                                                     |
                                                    GND
```

5. Do not mount the phototransistor permanently yet -- it will be positioned in
   the sensor tube later (step 8).
6. **Quick test**: Power up the board via USB-C. Shine a phone torch at the
   phototransistor. Measure the voltage on GPIO2 -- it should rise from near 0V
   to above 1V. In darkness it should sit below 0.3V. If it reads high all the
   time, check the resistor value and that collector/emitter are not swapped.

## 4. Connect the RTC Module

The DS3231 provides accurate timestamps that persist through deep sleep and
power loss.

1. Connect **VCC** on the RTC module to the **3.3V rail**.
2. Connect **GND** on the RTC module to the **GND rail**.
3. Connect **SDA** to **GPIO8**.
4. Connect **SCL** to **GPIO9**.

That is it -- the module has its own pull-up resistors on the I2C lines.

**Note**: If your DS3231 module is the ZS-042 type with a charging circuit for
the backup battery, see the [schematic notes](schematic.md#ds3231-rtc-module)
about removing the charging resistor if using a standard CR2032.

## 5. Wire the SD Card Module

The MicroSD breakout connects over SPI.

1. Connect **VCC** to the **3.3V rail**.
2. Connect **GND** to the **GND rail**.
3. Connect **MOSI** (or DI) to **GPIO6**.
4. Connect **MISO** (or DO) to **GPIO5**.
5. Connect **CLK** (or SCLK) to **GPIO4**.
6. Connect **CS** to **GPIO7**.

**Quick test**: Insert a FAT32-formatted MicroSD card. If you have the firmware
ready, you can test by writing a file. Otherwise, just verify the wiring
visually for now.

## 6. Add the Button

The button activates WiFi AP mode.

1. Place the tactile button on the board.
2. Connect one side to **GPIO3**.
3. Connect the other side to **GND**.

No external resistor is needed. The firmware enables the internal pull-up on
GPIO3. When the button is pressed, GPIO3 is pulled to GND, which the firmware
detects as a button press.

Position the button so it will be accessible through the enclosure wall. You can
either:
- Mount it on a short pair of wires so it sits in a hole drilled in the
  enclosure, or
- Use a panel-mount waterproof button and wire it to GPIO3/GND.

## 7. Battery Connection

1. Place the 18650 battery in its holder.
2. Connect the **positive (+)** wire from the battery holder to the **VIN / 5V**
   pin on the ESP32-C3.
3. Connect the **negative (-)** wire to the **GND** rail.

**Do not connect the battery to the 3.3V pin.** The VIN pin feeds the onboard
regulator which handles the 3.0V-4.2V range of a single 18650 cell.

**Quick test**: With the battery inserted, the ESP32-C3 should power up (you
will see an LED flash briefly). If nothing happens, check the battery is charged
and the polarity is correct.

**Tip**: Add a small slide switch in the positive battery wire so you can turn
the device off without removing the battery. This is optional but convenient.

## 8. Light Collimator Construction

The collimator is a narrow tube that restricts the phototransistor's field of
view so it only sees the crossing warning lights.

1. Cut a drinking straw (or pen tube, or any opaque tube ~5mm diameter) to about
   **5cm long**.
2. If the tube is translucent, wrap it in electrical tape or paint the inside
   with a black marker to reduce light leakage and internal reflections.
3. Thread the phototransistor into one end of the tube with the sensor window
   facing inward (into the tube). The leads come out the back.
4. Secure the phototransistor with a small piece of heat-shrink tubing, a dab of
   hot glue, or a wrap of tape. Make sure no light can leak in around the edges.
5. The phototransistor leads connect back to the circuit on the board via short
   hookup wires (if not already soldered).

```
  [open end] ========== tube ========== [PT sensor] --wires--> board
   aims at                               sealed end
   lights
```

6. Test the collimator by shining a torch straight into the open end from 30cm
   away -- GPIO2 should read high. Move the torch to 45 degrees off-axis -- it
   should read low. If it still reads high off-axis, the tube needs to be longer
   or narrower.

## 9. Enclosure Preparation

A clip-lid food container works well. Choose one large enough for the board,
battery holder, and wiring, but small enough to be unobtrusive.

1. **Sensor hole**: Drill a hole (5-6mm) in one wall of the enclosure at the
   height where the sensor tube will sit. This is the end that faces the
   crossing lights.
2. **Insert the sensor tube**: Push the collimator tube through the hole from
   the inside so 1-2cm sticks out. Seal around it with hot glue or silicone on
   both sides.
3. **Button access**: Either drill a small hole for the button to poke through,
   or mount a panel-mount button in the wall. Seal with silicone.
4. **Tilt**: Angle the sensor tube very slightly downward (~5 degrees) so rain
   runs off rather than pooling in the open end.
5. **Drainage**: Optionally drill a tiny hole (1-2mm) in the bottom of the
   enclosure so any condensation can drain out.
6. Place a small bag of **silica gel** inside the enclosure to absorb moisture.
7. Close the lid and check everything is secure. The enclosure should sit flat
   and stable on the ground or on a wall.

## 10. Final Testing Checklist

Before deploying the device, run through this checklist:

| Test | How | Expected result |
|---|---|---|
| Power on | Insert battery | LED flashes briefly, device enters deep sleep |
| Light detection | Shine torch into sensor tube | Device wakes, GPIO2 goes high |
| Pattern rejection | Single brief flash | No state change logged |
| Pattern detection | Flash torch 4+ times in 4 seconds | CLOSED event logged |
| Open detection | Stop flashing, wait 5+ seconds | OPEN event logged |
| RTC time | Activate WiFi, check status page | Time is correct (sync if needed) |
| SD card write | Activate WiFi, download CSV | Log file contains test events |
| WiFi AP mode | Press button | Hotspot "RoundstoneSensor" appears |
| WiFi auto-off | Wait 5 minutes after activating | Hotspot disappears, device sleeps |
| Button from sleep | Press button while device is sleeping | WiFi activates |
| Battery life | Check voltage on status page | Above 3.3V for a fresh cell |

If all tests pass, the device is ready to deploy.

## Next Steps

- Flash the firmware from `device/firmware/barrier_logger/` to the ESP32-C3
  (see firmware README when available).
- Refer to the [main device README](../README.md) for deployment positioning
  and safety notes.
- Download your first real logs and place them in `data/ground_truth/` for
  analysis.
