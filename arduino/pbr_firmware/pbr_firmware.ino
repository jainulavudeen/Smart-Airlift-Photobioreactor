/*
 * Smart Airlift Photobioreactor - REFERENCE firmware (Arduino Uno)
 * ---------------------------------------------------------------
 * Implements docs/SERIAL_PROTOCOL.md so the web application can talk to the
 * hardware. Adapt pins / sensor / RTC types to the actual circuit built by the
 * hardware team - only the serial messages must stay the same.
 *
 * Assumed hardware (change if different):
 *   - DS18B20 waterproof temperature probe on D2 (4.7k pull-up to 5V)
 *   - DS3231 RTC on I2C (A4 = SDA, A5 = SCL)
 *   - LED strip switched by a logic-level MOSFET / relay on D9
 *
 * Libraries (Arduino Library Manager):
 *   OneWire, DallasTemperature, RTClib (Adafruit)
 *
 * Temperature is MONITORED ONLY - this firmware does not control heating/cooling.
 */
#include <OneWire.h>
#include <DallasTemperature.h>
#include <RTClib.h>
#include <EEPROM.h>

#define FW_VERSION   "PBR-FW-1.0"
#define PIN_ONEWIRE  2
#define PIN_LED      9
#define LED_ACTIVE   HIGH          // set LOW for active-low relay modules

OneWire oneWire(PIN_ONEWIRE);
DallasTemperature sensors(&oneWire);
RTC_DS3231 rtc;

// ---- persistent configuration (EEPROM) ----
struct Config {
  uint8_t  magic;                 // 0xA5 when valid
  uint16_t startMin;              // LED ON  time, minutes after midnight
  uint16_t stopMin;               // LED OFF time
  uint8_t  autoMode;              // 1 = follow schedule, 0 = manual
  uint8_t  manualLed;             // LED state in manual mode
  uint16_t intervalS;             // DATA reporting period
} cfg;

bool rtcOk = false;
float lastTemp = NAN;
unsigned long lastReport = 0;
unsigned long convStarted = 0;
bool ledOn = false;

char rx[65];
uint8_t rxLen = 0;

// ------------------------------------------------------------------ config
void loadConfig() {
  EEPROM.get(0, cfg);
  if (cfg.magic != 0xA5) {
    cfg = {0xA5, 6 * 60, 18 * 60, 1, 0, 2};
    EEPROM.put(0, cfg);
  }
}
void saveConfig() { EEPROM.put(0, cfg); }

// ------------------------------------------------------------------ helpers
bool scheduleOn(uint16_t nowMin) {
  if (cfg.startMin == cfg.stopMin) return false;
  if (cfg.startMin < cfg.stopMin) return nowMin >= cfg.startMin && nowMin < cfg.stopMin;
  return nowMin >= cfg.startMin || nowMin < cfg.stopMin;  // overnight window
}

void applyLed() {
  bool want = cfg.manualLed;
  if (cfg.autoMode && rtcOk) {
    DateTime n = rtc.now();
    want = scheduleOn(n.hour() * 60 + n.minute());
  }
  if (want != ledOn) {
    ledOn = want;
    digitalWrite(PIN_LED, ledOn ? LED_ACTIVE : !LED_ACTIVE);
  }
}

bool parseHHMM(const char *s, uint16_t &out) {
  int h, m;
  if (!s || sscanf(s, "%d:%d", &h, &m) != 2 || h < 0 || h > 23 || m < 0 || m > 59) return false;
  out = h * 60 + m;
  return true;
}

void sendData() {
  Serial.print(F("DATA,"));
  if (isnan(lastTemp)) Serial.print(F("NAN")); else Serial.print(lastTemp, 2);
  Serial.print(','); Serial.print(ledOn ? 1 : 0);
  Serial.print(','); Serial.print(cfg.autoMode ? 'A' : 'M');
  Serial.print(',');
  if (rtcOk) {
    char buf[20];
    DateTime n = rtc.now();
    snprintf(buf, sizeof buf, "%04d-%02d-%02d %02d:%02d:%02d",
             n.year(), n.month(), n.day(), n.hour(), n.minute(), n.second());
    Serial.print(buf);
  }
  Serial.println();
}

// ------------------------------------------------------------------ commands
void handleCommand(char *line) {
  for (char *p = line; *p; p++) *p = toupper(*p);
  char *cmd = strtok(line, ",");
  char *a1 = strtok(NULL, ",");
  char *a2 = strtok(NULL, ",");
  if (!cmd) return;

  if (!strcmp(cmd, "PING")) {
    Serial.println(F("PONG"));
  } else if (!strcmp(cmd, "HELLO")) {
    Serial.println(F("READY," FW_VERSION));
  } else if (!strcmp(cmd, "GET")) {
    sendData();
  } else if (!strcmp(cmd, "LED") && a1 && (!strcmp(a1, "ON") || !strcmp(a1, "OFF"))) {
    cfg.autoMode = 0;
    cfg.manualLed = !strcmp(a1, "ON");
    saveConfig(); applyLed();
    Serial.print(F("ACK,LED,")); Serial.println(a1);
    sendData();
  } else if (!strcmp(cmd, "MODE") && a1 && (!strcmp(a1, "AUTO") || !strcmp(a1, "MANUAL"))) {
    if (!strcmp(a1, "MANUAL")) { cfg.manualLed = ledOn; cfg.autoMode = 0; }
    else cfg.autoMode = 1;
    saveConfig(); applyLed();
    Serial.print(F("ACK,MODE,")); Serial.println(a1);
    sendData();
  } else if (!strcmp(cmd, "SCHED")) {
    uint16_t s, e;
    if (parseHHMM(a1, s) && parseHHMM(a2, e)) {
      cfg.startMin = s; cfg.stopMin = e; saveConfig(); applyLed();
      Serial.print(F("ACK,SCHED,")); Serial.print(a1); Serial.print(','); Serial.println(a2);
    } else Serial.println(F("ERR,BAD_ARGS,SCHED"));
  } else if (!strcmp(cmd, "INTERVAL") && a1) {
    long v = atol(a1);
    if (v >= 1 && v <= 3600) {
      cfg.intervalS = v; saveConfig();
      Serial.print(F("ACK,INTERVAL,")); Serial.println(v);
    } else Serial.println(F("ERR,BAD_ARGS,INTERVAL"));
  } else if (!strcmp(cmd, "TIME") && a1) {
    int Y, M, D, h, m, s;
    if (rtcOk && sscanf(a1, "%d-%d-%d %d:%d:%d", &Y, &M, &D, &h, &m, &s) == 6) {
      rtc.adjust(DateTime(Y, M, D, h, m, s));
      Serial.println(F("ACK,TIME"));
    } else Serial.println(rtcOk ? F("ERR,BAD_ARGS,TIME") : F("ERR,RTC"));
  } else {
    Serial.println(F("ERR,UNKNOWN_CMD"));
  }
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      rx[rxLen] = 0;
      if (rxLen) handleCommand(rx);
      rxLen = 0;
    } else if (rxLen < sizeof(rx) - 1) {
      rx[rxLen++] = c;
    }
  }
}

// ------------------------------------------------------------------ main
void setup() {
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, !LED_ACTIVE);
  Serial.begin(9600);
  loadConfig();

  rtcOk = rtc.begin();
  if (!rtcOk) Serial.println(F("ERR,RTC"));

  sensors.begin();
  if (sensors.getDeviceCount() == 0) Serial.println(F("ERR,SENSOR"));
  sensors.setResolution(12);
  sensors.setWaitForConversion(false);   // non-blocking: keep serial responsive
  sensors.requestTemperatures();
  convStarted = millis();

  applyLed();
  Serial.println(F("READY," FW_VERSION));
}

void loop() {
  readSerial();

  // 12-bit DS18B20 conversion takes ~750 ms
  if (millis() - convStarted >= 800) {
    float t = sensors.getTempCByIndex(0);
    lastTemp = (t == DEVICE_DISCONNECTED_C || t == 85.0) ? NAN : t;
    sensors.requestTemperatures();
    convStarted = millis();
  }

  applyLed();

  if (millis() - lastReport >= (unsigned long)cfg.intervalS * 1000UL) {
    lastReport = millis();
    sendData();
  }
}
