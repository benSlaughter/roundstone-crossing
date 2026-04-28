/*
 * Roundstone Crossing Barrier Logger
 * ===================================
 * ESP32-C3 SuperMini firmware that logs level crossing barrier open/close
 * events by detecting the flashing red warning lights via a phototransistor.
 *
 * Detection: Warning lights flash at ~1Hz. We count pulses and require 3+
 * pulses within 4 seconds to confirm barriers are down. 5 seconds without
 * pulses means barriers are up. Only state transitions are logged.
 *
 * Power: Deep sleep (~5uA) as default. Wakes on GPIO2 rising edge (light
 * detected), monitors for flashing pattern, logs events, returns to sleep.
 *
 * WiFi AP: Button on GPIO3 activates AP mode for log download and time sync.
 *
 * Required libraries:
 *   - WiFi.h        (built-in ESP32)
 *   - WebServer.h   (built-in ESP32)
 *   - Wire.h        (built-in, I2C for RTC)
 *   - SPI.h         (built-in, for SD card)
 *   - SD.h          (built-in ESP32)
 *   - RTClib.h      (Adafruit RTClib — install via Arduino Library Manager)
 */

#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <RTClib.h>

// ---------------------------------------------------------------------------
// Pin definitions
// ---------------------------------------------------------------------------
#define PIN_LIGHT_SENSOR  2   // Phototransistor input (HIGH = light detected)
#define PIN_BUTTON        3   // WiFi AP mode button (active LOW, internal pullup)
#define SD_CLK            4
#define SD_MISO           5
#define SD_MOSI           6
#define SD_CS             7
#define RTC_SDA           8
#define RTC_SCL           9
#define PIN_BATTERY       0   // ADC input for battery voltage (via voltage divider)
#define PIN_LED          10   // Status LED (built-in on some SuperMini boards)

// ---------------------------------------------------------------------------
// Battery monitoring
// ---------------------------------------------------------------------------
// Voltage divider: 100kOhm + 100kOhm from battery to GND, midpoint to GPIO0
// This halves the battery voltage so 4.2V -> 2.1V (within ESP32 ADC range)
// Calibration: adjust these if your readings are off
#define BATTERY_DIVIDER_RATIO  2.0
#define BATTERY_ADC_VREF       3.3
#define BATTERY_LOW_VOLTS      3.3   // Log warning below this
#define BATTERY_CRITICAL_VOLTS 3.0   // Stop logging below this

// ---------------------------------------------------------------------------
// Timing constants (milliseconds unless noted)
// ---------------------------------------------------------------------------
#define PULSE_CONFIRM_WINDOW_MS   4000  // Window to count pulses for pattern
#define PULSE_THRESHOLD           3     // Min pulses in window to confirm flashing
#define OPEN_TIMEOUT_MS           5000  // No pulses for this long = barriers up
#define MONITOR_CHECK_INTERVAL_MS 500   // Poll interval during active monitoring
#define INITIAL_MONITOR_MS        6000  // Max time to confirm flashing on wake
#define DEBOUNCE_MS               50    // Pulse debounce time
#define AP_TIMEOUT_MS             300000 // WiFi AP auto-shutdown (5 minutes)
#define AP_INACTIVITY_MS          300000 // Inactivity timeout for AP mode

// ---------------------------------------------------------------------------
// WiFi AP settings
// ---------------------------------------------------------------------------
#define AP_SSID     "RXLogger"
#define AP_PASSWORD "roundstone"
#define AP_IP       IPAddress(192, 168, 4, 1)

// ---------------------------------------------------------------------------
// File paths
// ---------------------------------------------------------------------------
#define LOG_FILE "/barrier_log.csv"

// ---------------------------------------------------------------------------
// Crossing states
// ---------------------------------------------------------------------------
enum CrossingState {
  STATE_UNKNOWN,
  STATE_OPEN,
  STATE_CLOSED
};

// ---------------------------------------------------------------------------
// RTC-persistent data (survives deep sleep)
// ---------------------------------------------------------------------------
RTC_DATA_ATTR CrossingState lastKnownState = STATE_UNKNOWN;
RTC_DATA_ATTR uint32_t bootCount = 0;

// ---------------------------------------------------------------------------
// Globals
// ---------------------------------------------------------------------------
RTC_DS3231 rtc;
bool rtcAvailable = false;
bool sdAvailable = false;
WebServer server(80);
unsigned long apStartTime = 0;
unsigned long lastClientActivity = 0;

// ---------------------------------------------------------------------------
// Forward declarations
// ---------------------------------------------------------------------------
void enterDeepSleep();
void monitorCrossing();
void logEvent(const char* state);
void startAPMode();
void handleRoot();
void handleDownload();
void handleTimeSync();
void handleClear();
void handleDebug();
void handleDebugStream();
float readBatteryVoltage();
void blinkLED(int count, int onMs, int offMs);
String getTimestamp();
String formatUptime(unsigned long ms);

// ===========================================================================
// setup()
// ===========================================================================
void setup() {
  Serial.begin(115200);
  delay(100);
  bootCount++;

  // --- Status LED ---
  pinMode(PIN_LED, OUTPUT);
  blinkLED(2, 50, 50);  // Quick double-blink on any wake

  Serial.println();
  Serial.println("=== Barrier Logger ===");
  Serial.print("Boot #");
  Serial.println(bootCount);

  // --- Battery check ---
  float battV = readBatteryVoltage();
  Serial.print("Battery: ");
  Serial.print(battV, 2);
  Serial.println("V");
  if (battV > 0 && battV < BATTERY_CRITICAL_VOLTS) {
    Serial.println("CRITICAL: Battery too low, sleeping indefinitely");
    blinkLED(10, 100, 100);  // Rapid blink = low battery warning
    enterDeepSleep();
    return;
  }

  // --- Initialise I2C for RTC ---
  Wire.begin(RTC_SDA, RTC_SCL);
  if (rtc.begin(&Wire)) {
    rtcAvailable = true;
    if (rtc.lostPower()) {
      Serial.println("WARNING: RTC lost power, time may be wrong");
    }
    Serial.print("RTC time: ");
    Serial.println(getTimestamp());
  } else {
    Serial.println("WARNING: RTC not found, timestamps will be unavailable");
  }

  // --- Initialise SPI and SD card ---
  SPI.begin(SD_CLK, SD_MISO, SD_MOSI, SD_CS);
  if (SD.begin(SD_CS)) {
    sdAvailable = true;
    Serial.println("SD card initialised");
  } else {
    Serial.println("WARNING: SD card not found, logging disabled");
  }

  // --- Check wake reason ---
  esp_sleep_wakeup_cause_t wakeReason = esp_sleep_get_wakeup_cause();
  Serial.print("Wake reason: ");
  Serial.println(wakeReason);

  // --- Check button for AP mode (GPIO3 LOW = pressed) ---
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  delay(50); // Let pin settle
  if (digitalRead(PIN_BUTTON) == LOW) {
    Serial.println("Button pressed — entering AP mode");
    startAPMode();
    return; // loop() will handle AP server
  }

  // --- Log boot event on fresh power-on (not GPIO wake) ---
  if (wakeReason != ESP_SLEEP_WAKEUP_GPIO
      && wakeReason != ESP_SLEEP_WAKEUP_EXT0
      && wakeReason != ESP_SLEEP_WAKEUP_EXT1) {
    logEvent("BOOT");
    lastKnownState = STATE_UNKNOWN;
  }

  // --- Set up light sensor pin ---
  pinMode(PIN_LIGHT_SENSOR, INPUT);

  // --- Enter crossing monitoring ---
  monitorCrossing();

  // --- Done monitoring, go to sleep ---
  enterDeepSleep();
}

// ===========================================================================
// loop() — only used during AP mode
// ===========================================================================
void loop() {
  if (apStartTime > 0) {
    server.handleClient();

    // Auto-shutdown AP after inactivity timeout
    if (millis() - lastClientActivity > AP_INACTIVITY_MS) {
      Serial.println("AP inactivity timeout — shutting down");
      WiFi.softAPdisconnect(true);
      WiFi.mode(WIFI_OFF);
      delay(100);
      enterDeepSleep();
    }
  }
}

// ===========================================================================
// monitorCrossing()
// Actively monitor the phototransistor for the flashing warning light pattern.
// Count HIGH->LOW transitions (pulses). If 3+ pulses in 4 seconds, the
// crossing is closed. Stay awake while flashing continues. Once 5 seconds
// pass with no pulses, log OPEN and return (caller will deep sleep).
// ===========================================================================
void monitorCrossing() {
  Serial.println("Entering active monitoring mode");

  unsigned long monitorStart = millis();
  unsigned long windowStart = millis();
  unsigned long lastPulseTime = 0;
  int pulseCount = 0;
  bool lastLightState = digitalRead(PIN_LIGHT_SENSOR);
  bool flashingConfirmed = false;

  // Phase 1: Try to confirm flashing pattern within INITIAL_MONITOR_MS
  while (millis() - monitorStart < INITIAL_MONITOR_MS) {
    bool currentLight = digitalRead(PIN_LIGHT_SENSOR);

    // Detect HIGH -> LOW transition (end of a light pulse)
    if (lastLightState == HIGH && currentLight == LOW) {
      unsigned long now = millis();
      // Debounce
      if (lastPulseTime == 0 || (now - lastPulseTime) > DEBOUNCE_MS) {
        pulseCount++;
        lastPulseTime = now;
        Serial.print("Pulse #");
        Serial.println(pulseCount);
      }
    }
    lastLightState = currentLight;

    // Check if we have enough pulses within the confirmation window
    if (pulseCount >= PULSE_THRESHOLD) {
      unsigned long elapsed = millis() - windowStart;
      if (elapsed <= PULSE_CONFIRM_WINDOW_MS) {
        flashingConfirmed = true;
        Serial.println("Flashing pattern confirmed — crossing CLOSED");
        break;
      } else {
        // Window expired, reset and try again
        pulseCount = 0;
        windowStart = millis();
      }
    }

    delay(5); // Small delay to avoid busy-waiting
  }

  // If no flashing detected, it was probably a car headlight or ambient light
  if (!flashingConfirmed) {
    Serial.println("No flashing pattern detected — false wake, returning to sleep");
    return;
  }

  // Log CLOSED transition if state changed
  if (lastKnownState != STATE_CLOSED) {
    logEvent("CLOSED");
    lastKnownState = STATE_CLOSED;
  }

  // Phase 2: Stay awake while flashing continues.
  // Check periodically; if no pulses for OPEN_TIMEOUT_MS, crossing is open.
  lastPulseTime = millis();
  lastLightState = digitalRead(PIN_LIGHT_SENSOR);

  while (true) {
    bool currentLight = digitalRead(PIN_LIGHT_SENSOR);

    // Detect HIGH -> LOW transition
    if (lastLightState == HIGH && currentLight == LOW) {
      unsigned long now = millis();
      if (now - lastPulseTime > DEBOUNCE_MS) {
        lastPulseTime = now;
      }
    }
    lastLightState = currentLight;

    // Check for open timeout
    if (millis() - lastPulseTime > OPEN_TIMEOUT_MS) {
      Serial.println("No pulses for 5s — crossing OPEN");
      if (lastKnownState != STATE_OPEN) {
        logEvent("OPEN");
        lastKnownState = STATE_OPEN;
      }
      return;
    }

    // Also check button during monitoring (allow entering AP mode)
    if (digitalRead(PIN_BUTTON) == LOW) {
      Serial.println("Button pressed during monitoring — entering AP mode");
      startAPMode();
      return;
    }

    delay(5);
  }
}

// ===========================================================================
// logEvent() — write timestamp + state to the SD card log
// ===========================================================================
void logEvent(const char* state) {
  String ts = getTimestamp();
  String line = ts + "," + state;

  Serial.print("LOG: ");
  Serial.println(line);

  if (!sdAvailable) {
    Serial.println("SD not available — event not saved");
    return;
  }

  File f = SD.open(LOG_FILE, FILE_APPEND);
  if (!f) {
    Serial.println("ERROR: Could not open log file for writing");
    return;
  }

  f.println(line);
  f.flush();
  f.close();
  Serial.println("Event written to SD");
}

// ===========================================================================
// getTimestamp() — return ISO 8601 string from RTC, or uptime fallback
// ===========================================================================
String getTimestamp() {
  if (!rtcAvailable) {
    // Fallback: use millis-based placeholder
    return "1970-01-01T00:00:00";
  }

  DateTime now = rtc.now();
  char buf[21];
  snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02d",
           now.year(), now.month(), now.day(),
           now.hour(), now.minute(), now.second());
  return String(buf);
}

// ===========================================================================
// enterDeepSleep() — configure GPIO2 as wake source and enter deep sleep
// ===========================================================================
void enterDeepSleep() {
  Serial.println("Entering deep sleep...");
  Serial.flush();

  // Configure GPIO2 rising edge as wake source
  // On ESP32-C3, use esp_deep_sleep_enable_gpio_wakeup
  esp_deep_sleep_enable_gpio_wakeup(1ULL << PIN_LIGHT_SENSOR,
                                     ESP_GPIO_WAKEUP_GPIO_HIGH);

  delay(10);
  esp_deep_sleep_start();
}

// ===========================================================================
// WiFi AP mode and web server
// ===========================================================================

// Status page HTML served at /
static const char STATUS_PAGE_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RXLogger</title>
<style>
  body { font-family: sans-serif; max-width: 600px; margin: 20px auto; padding: 0 10px; }
  h1 { color: #333; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0; }
  td, th { border: 1px solid #ccc; padding: 8px; text-align: left; }
  th { background: #f0f0f0; }
  a { display: inline-block; margin: 5px 0; padding: 8px 16px; background: #0366d6;
      color: white; text-decoration: none; border-radius: 4px; }
  a.danger { background: #d73a49; }
  #status { color: #666; font-size: 0.9em; }
</style>
</head>
<body>
<h1>Roundstone Crossing Logger</h1>
<table>
  <tr><th>Uptime</th><td id="uptime">%UPTIME%</td></tr>
  <tr><th>RTC Time</th><td id="rtctime">%RTCTIME%</td></tr>
  <tr><th>Last Event</th><td>%LASTEVENT%</td></tr>
  <tr><th>Total Events</th><td>%TOTALEVENTS%</td></tr>
  <tr><th>Boot Count</th><td>%BOOTCOUNT%</td></tr>
  <tr><th>Battery</th><td>%BATTERY%</td></tr>
  <tr><th>SD Card</th><td>%SDSTATUS%</td></tr>
  <tr><th>RTC</th><td>%RTCSTATUS%</td></tr>
</table>
<p>
  <a href="/download">Download Log (CSV)</a>
  <a href="/debug">Live Debug</a>
  <a href="/clear" class="danger" onclick="return confirm('Clear all log data?')">Clear Log</a>
</p>
<p id="status">Time sync: waiting...</p>
<script>
// Auto-sync time from the browser to the device RTC on page load
(function() {
  var ts = Math.floor(Date.now() / 1000);
  fetch('/time?t=' + ts)
    .then(function(r) { return r.text(); })
    .then(function(t) {
      document.getElementById('status').textContent = 'Time synced: ' + t;
    })
    .catch(function() {
      document.getElementById('status').textContent = 'Time sync failed';
    });
})();
</script>
</body>
</html>
)rawliteral";

void startAPMode() {
  apStartTime = millis();
  lastClientActivity = millis();

  WiFi.mode(WIFI_AP);
  WiFi.softAPConfig(AP_IP, AP_IP, IPAddress(255, 255, 255, 0));
  WiFi.softAP(AP_SSID, AP_PASSWORD);

  Serial.print("AP started — SSID: ");
  Serial.println(AP_SSID);
  Serial.print("IP: ");
  Serial.println(WiFi.softAPIP());

  server.on("/", handleRoot);
  server.on("/download", handleDownload);
  server.on("/time", handleTimeSync);
  server.on("/clear", handleClear);
  server.on("/debug", handleDebug);
  server.on("/debug/stream", handleDebugStream);
  server.begin();

  Serial.println("Web server started");
}

// Count events and get the last line from the log file
void getLogStats(int &totalEvents, String &lastEvent) {
  totalEvents = 0;
  lastEvent = "none";

  if (!sdAvailable) return;

  File f = SD.open(LOG_FILE, FILE_READ);
  if (!f) return;

  String line;
  while (f.available()) {
    line = f.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      totalEvents++;
      lastEvent = line;
    }
  }
  f.close();
}

void handleRoot() {
  lastClientActivity = millis();

  int totalEvents = 0;
  String lastEvent;
  getLogStats(totalEvents, lastEvent);

  String page = String(STATUS_PAGE_HTML);
  page.replace("%UPTIME%", formatUptime(millis()));
  page.replace("%RTCTIME%", getTimestamp());
  page.replace("%LASTEVENT%", lastEvent);
  page.replace("%TOTALEVENTS%", String(totalEvents));
  page.replace("%BOOTCOUNT%", String(bootCount));

  float battV = readBatteryVoltage();
  String battStr = String(battV, 2) + "V";
  if (battV < BATTERY_LOW_VOLTS && battV > 0) battStr += " (LOW)";
  page.replace("%BATTERY%", battStr);

  page.replace("%SDSTATUS%", sdAvailable ? "OK" : "Not found");
  page.replace("%RTCSTATUS%", rtcAvailable ? "OK" : "Not found");

  server.send(200, "text/html", page);
}

void handleDownload() {
  lastClientActivity = millis();

  if (!sdAvailable) {
    server.send(503, "text/plain", "SD card not available");
    return;
  }

  File f = SD.open(LOG_FILE, FILE_READ);
  if (!f) {
    server.send(404, "text/plain", "No log file found");
    return;
  }

  server.sendHeader("Content-Disposition", "attachment; filename=barrier_log.csv");
  server.streamFile(f, "text/csv");
  f.close();
}

void handleTimeSync() {
  lastClientActivity = millis();

  if (!server.hasArg("t")) {
    server.send(400, "text/plain", "Missing parameter: t (unix timestamp)");
    return;
  }

  long unixTime = server.arg("t").toInt();
  if (unixTime < 1000000000L) {
    server.send(400, "text/plain", "Invalid timestamp");
    return;
  }

  if (rtcAvailable) {
    rtc.adjust(DateTime((uint32_t)unixTime));
    String newTime = getTimestamp();
    Serial.print("RTC set to: ");
    Serial.println(newTime);
    server.send(200, "text/plain", newTime);
  } else {
    server.send(503, "text/plain", "RTC not available");
  }
}

void handleClear() {
  lastClientActivity = millis();

  if (!sdAvailable) {
    server.send(503, "text/plain", "SD card not available");
    return;
  }

  SD.remove(LOG_FILE);
  Serial.println("Log file cleared");
  logEvent("CLEARED");
  server.send(200, "text/plain", "Log cleared. A CLEARED event has been recorded.");
}

// ===========================================================================
// Debug / calibration page — live sensor readout for aiming and testing
// ===========================================================================

static const char DEBUG_PAGE_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RXLogger Debug</title>
<style>
  body { font-family: monospace; max-width: 600px; margin: 20px auto; padding: 0 10px;
         background: #1a1a2e; color: #e0e0e0; }
  h1 { color: #00d4ff; }
  .sensor-bar { width: 100%; height: 40px; background: #333; border-radius: 4px;
                margin: 10px 0; overflow: hidden; }
  .sensor-fill { height: 100%; transition: width 0.15s; border-radius: 4px; }
  .light-on .sensor-fill { background: #ff4444; width: 100%; }
  .light-off .sensor-fill { background: #444; width: 5%; }
  .indicator { font-size: 3em; text-align: center; margin: 20px 0; }
  .pulse-count { font-size: 1.5em; text-align: center; margin: 10px 0; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0; }
  td, th { border: 1px solid #444; padding: 6px; text-align: left; }
  th { background: #2a2a4e; }
  .log { background: #111; padding: 10px; border-radius: 4px; height: 200px;
         overflow-y: auto; font-size: 0.85em; white-space: pre; margin-top: 10px; }
  a { color: #00d4ff; }
  .open { color: #44ff44; }
  .closed { color: #ff4444; }
  .unknown { color: #ffaa00; }
</style>
</head>
<body>
<h1>Live Sensor Debug</h1>
<p><a href="/">Back to status</a></p>

<div class="indicator" id="light">-</div>
<div class="sensor-bar" id="bar"><div class="sensor-fill"></div></div>

<table>
  <tr><th>Sensor GPIO</th><td id="gpio">-</td></tr>
  <tr><th>Pulses (4s window)</th><td id="pulses">-</td></tr>
  <tr><th>Detected State</th><td id="state">-</td></tr>
  <tr><th>Battery</th><td id="battery">-</td></tr>
  <tr><th>Threshold</th><td>%THRESHOLD% pulses in %WINDOW%ms</td></tr>
  <tr><th>Samples</th><td id="samples">0</td></tr>
</table>

<h3>Event Log</h3>
<div class="log" id="log"></div>

<script>
var samples = 0;
var pulseTimestamps = [];
var lastGpio = 0;
var WINDOW = %WINDOW%;
var THRESHOLD = %THRESHOLD%;

function poll() {
  fetch('/debug/stream')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      samples++;
      var now = Date.now();

      // Update GPIO display
      document.getElementById('gpio').textContent = d.gpio ? 'HIGH (light)' : 'LOW (dark)';
      var bar = document.getElementById('bar');
      bar.className = 'sensor-bar ' + (d.gpio ? 'light-on' : 'light-off');

      // Big indicator
      var light = document.getElementById('light');
      light.textContent = d.gpio ? 'LIGHT DETECTED' : 'dark';
      light.style.color = d.gpio ? '#ff4444' : '#666';

      // Track pulses (HIGH -> LOW transitions)
      if (lastGpio === 1 && d.gpio === 0) {
        pulseTimestamps.push(now);
        addLog('Pulse detected');
      }
      lastGpio = d.gpio;

      // Clean old pulses outside window
      pulseTimestamps = pulseTimestamps.filter(function(t) { return now - t < WINDOW; });
      var pc = pulseTimestamps.length;
      document.getElementById('pulses').textContent = pc + ' / ' + THRESHOLD + ' needed';

      // State detection
      var stateEl = document.getElementById('state');
      if (pc >= THRESHOLD) {
        stateEl.textContent = 'CLOSED (flashing detected)';
        stateEl.className = 'closed';
      } else if (pc > 0) {
        stateEl.textContent = 'DETECTING... (' + pc + ' pulses)';
        stateEl.className = 'unknown';
      } else {
        stateEl.textContent = 'OPEN (no flashing)';
        stateEl.className = 'open';
      }

      document.getElementById('samples').textContent = samples;
      if (d.battery !== undefined) {
        document.getElementById('battery').textContent = d.battery.toFixed(2) + 'V';
      }
    })
    .catch(function(e) { addLog('ERROR: ' + e); });
}

function addLog(msg) {
  var log = document.getElementById('log');
  var time = new Date().toLocaleTimeString();
  log.textContent += time + '  ' + msg + '\n';
  log.scrollTop = log.scrollHeight;
}

addLog('Debug started — polling sensor every 200ms');
addLog('Aim sensor at crossing light and watch for pulses');
setInterval(poll, 200);
</script>
</body>
</html>
)rawliteral";

void handleDebug() {
  lastClientActivity = millis();

  String page = String(DEBUG_PAGE_HTML);
  page.replace("%THRESHOLD%", String(PULSE_THRESHOLD));
  page.replace("%WINDOW%", String(PULSE_CONFIRM_WINDOW_MS));

  server.send(200, "text/html", page);
}

void handleDebugStream() {
  lastClientActivity = millis();

  int gpioState = digitalRead(PIN_LIGHT_SENSOR);
  float battV = readBatteryVoltage();
  String json = "{\"gpio\":" + String(gpioState) + ",\"battery\":" + String(battV, 2) + "}";
  server.send(200, "application/json", json);
}

// ===========================================================================
// Utility
// ===========================================================================

float readBatteryVoltage() {
  // Read ADC on battery voltage divider pin
  // Average multiple samples for stability
  long total = 0;
  for (int i = 0; i < 16; i++) {
    total += analogRead(PIN_BATTERY);
    delayMicroseconds(100);
  }
  float adcValue = total / 16.0;
  // Convert ADC reading to voltage (ESP32-C3 has 12-bit ADC, 0-3.3V range)
  float voltage = (adcValue / 4095.0) * BATTERY_ADC_VREF * BATTERY_DIVIDER_RATIO;
  return voltage;
}

void blinkLED(int count, int onMs, int offMs) {
  for (int i = 0; i < count; i++) {
    digitalWrite(PIN_LED, HIGH);
    delay(onMs);
    digitalWrite(PIN_LED, LOW);
    if (i < count - 1) delay(offMs);
  }
}

String formatUptime(unsigned long ms) {
  unsigned long secs = ms / 1000;
  unsigned long mins = secs / 60;
  unsigned long hrs = mins / 60;
  secs %= 60;
  mins %= 60;

  char buf[16];
  snprintf(buf, sizeof(buf), "%luh %lum %lus", hrs, mins, secs);
  return String(buf);
}
