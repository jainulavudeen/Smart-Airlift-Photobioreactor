# Smart Airlift Photobioreactor – Monitoring & Control Web App

A web dashboard for an airlift photobioreactor (PBR). It shows live temperature,
controls the LED photoperiod, manages culture runs, stores history with CSV
export, and talks to an **Arduino Uno over USB serial**. A built-in
**simulator** lets you test everything before the hardware is ready.

> Temperature is **monitored only**. The system raises alerts when the
> temperature leaves the set limits, but it does not heat or cool.

| Layer | Technology |
|---|---|
| Frontend | HTML, CSS, vanilla JavaScript, Chart.js 4 (bundled in `static/vendor`, works offline) |
| Backend | Python 3.9+, Flask, Flask-SocketIO (live push) |
| Database | SQLite (`pbr_data.db`, created on first run) |
| Hardware link | PySerial (line-based ASCII protocol) |

---

## 1. Setup

```bash
cd Divya_Project
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open **http://localhost:8000**. The app starts in **simulation mode**, so data
appears straight away.

* Use another port with `PBR_PORT=8080 python app.py` (Windows: `set PBR_PORT=8080`).
  Port 5000 is avoided because macOS AirPlay uses it.
* Other devices on the same network (tablet, phone) can open `http://<pc-ip>:8000`.
* Delete `pbr_data.db` to start with an empty database.

## 2. Connecting the real Arduino

1. Flash firmware that follows **`docs/SERIAL_PROTOCOL.md`**. A complete
   reference sketch is in `arduino/pbr_firmware/pbr_firmware.ino`
   (DS18B20 + DS3231 + MOSFET-driven LED strip). Change the pins and parts to
   match the real circuit.
2. Plug the Uno into the PC by USB and **close the Arduino IDE Serial Monitor**.
   Only one program can use the port at a time.
3. In the web app go to **Settings → Hardware connection**, choose
   **Arduino (USB serial)**, pick the port (↻ rescans), check the baud rate
   (9600), and click **Save & connect**.
4. The top bar changes from **SIMULATED DATA** (amber) to **LIVE HARDWARE**
   (green). Use **Settings → Serial console** to watch the raw traffic or send
   `PING` / `GET` by hand.
5. Optional: **Settings → Data management → Delete all simulated data** clears
   the test records before the first real experiment.

On Linux, add your user to the `dialout` group to get access to `/dev/ttyACM0`.

## 3. The six sections

| Section | What it does |
|---|---|
| **Dashboard** | Reactor state (Idle/Running + run ID), temperature with limit status, LED state/mode/schedule, live culture runtime, Arduino connection (port, firmware, last data), 30-minute chart, recent alerts and lighting events |
| **Live Monitoring** | Large live readout, real-time chart (5 min – 24 h, pause) with the limit band and LED-on shading, window min/max/avg, quick limit editor, alert list with acknowledge |
| **Lighting Control** | LED status, **Automatic / Manual** mode, manual **ON/OFF**, start/stop time with photoperiod (e.g. 12 h : 12 h), 24-hour timeline with a "now" marker and next change, lighting record table + CSV |
| **Culture Runs** | Start a run (name, organism, notes) → unique ID `RUN-YYYYMMDD-NNN`; live elapsed timer; stop with end notes; history with duration, reading count, source, per-run data and CSV |
| **Historical Data** | Filter by time range / custom dates, run and source; stats; chart (hardware and simulated in separate colours); tabs for readings, lighting records, runs and alerts, each with CSV export |
| **Settings** | Reactor details, temperature limits, sampling and recording intervals, data source (simulation / serial), port and baud, serial console, deletion of simulated data |

## 4. How it works

```
 Browser (6 pages)  ←— Socket.IO: status / reading / alert / led_event —┐
   │  REST /api/*                                                         │
   ▼                                                                       │
 app.py (Flask) ──► DeviceManager (device.py, background thread) ─────────┘
   │                    │  parses protocol, records data, raises alerts
   ▼                    ▼
 database.py      SimulatedLink  ──or──  SerialLink (PySerial → Arduino)
 (SQLite)         (virtual Arduino)       (/dev/ttyACM0, COM3 …)
```

* **One protocol, two links.** `SimulatedLink` is a virtual Arduino that
  accepts the same commands (`LED,ON`, `SCHED,…`) and sends the same `DATA,…`
  lines as the real firmware. The parsing, recording and alert code is
  therefore the same in both modes. Moving to hardware only changes the link
  object.
* **Simulated vs real data.** Every reading, lighting event, alert and run is
  saved with `source = simulated | hardware`. The UI shows this as a permanent
  top-bar badge and a striped banner in simulation mode. Every table row has
  a source badge, the history chart uses two colours, and every CSV has a
  `source` column. You can delete simulated data on its own.
* **Two intervals.** The *sampling interval* (default 2 s) sets how often the
  device reports, and it drives the live view. The *recording interval*
  (default 10 s) sets how often a reading is written to SQLite, which keeps
  the database small over long cultures.
* **The Arduino runs the lighting schedule.** The schedule is stored on the
  Arduino and driven by its RTC, so the photoperiod keeps running if the PC or
  browser is off. The web app sends the schedule, mode and clock when it
  connects and whenever they change.
* **Alerts.** Readings above or below the limits give a critical alert, with
  0.2 °C hysteresis to avoid repeats. Other alerts: sensor fault (`NAN` or
  DS18B20 fault values), connection lost (no data for max(10 s, 3 × interval)),
  and back-to-normal (info). The app reconnects to the serial port
  automatically.
* **Runs.** Only one run can be active. While it is active, every record is
  tagged with its `run_id`. The duration is computed from the start and end
  timestamps.

## 5. Project structure

```
app.py                 Flask routes, REST API, CSV export, Socket.IO
device.py              SimulatedLink, SerialLink, DeviceManager (protocol, alerts, recording)
database.py            SQLite schema + queries (settings, readings, lighting_events, runs, alerts)
templates/             base.html (sidebar/top bar) + one template per section
static/css/style.css   green theme, responsive layout
static/js/             app.js (shared), shared_widgets.js, one script per page
static/vendor/         Chart.js, date adapter, Socket.IO client (offline copies)
docs/API.md            REST + Socket.IO reference
docs/SERIAL_PROTOCOL.md  Arduino ⇄ PC protocol (integration contract)
arduino/pbr_firmware/  reference Uno sketch implementing the protocol
```

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| "Port open but device not responding" | Wrong port or baud rate, or the firmware does not send `DATA` lines. Check the Serial console |
| "could not open port … busy" | Close the Arduino IDE Serial Monitor or any other program using the port |
| No ports listed | Check the USB cable (some are charge-only). On Windows, install the CH340 driver for clone boards |
| Temperature shows `--` and a sensor-fault alert | Firmware sends `NAN`/−127: check the probe wiring and the 4.7 kΩ pull-up |
| LED time is wrong | The RTC is synced from the PC at every connect. Check the PC clock and timezone |

The built-in development server is fine for a single lab PC. For a shared or
always-on install, run the app behind a proper service (for example, systemd)
and do not expose it to the internet: there is no login.
