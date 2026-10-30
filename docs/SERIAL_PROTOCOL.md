# Serial Communication Protocol – Arduino ⇄ Web Application

This document is the contract between the **Arduino Uno firmware** and the
**Flask web application**. If the firmware implements the messages below, the web
app works with the real hardware with no code changes. The built-in simulator
(`device.py → SimulatedLink`) implements the same protocol, so anything that
works in simulation mode should also work with the hardware.

> **Scope:** temperature is **monitored only**. There are no commands for heating
> or cooling, and the firmware must not add any.

---

## 1. Physical layer

| Parameter        | Value                                              |
|------------------|----------------------------------------------------|
| Interface        | USB virtual COM port (Arduino Uno ATmega16U2)      |
| Baud rate        | **9600** (default; 19200–115200 can be set in Settings) |
| Framing          | 8 data bits, no parity, 1 stop bit (8N1)           |
| Encoding         | ASCII                                              |
| Line terminator  | `\n` (LF). A preceding `\r` is ignored             |
| Max line length  | 64 characters                                      |
| Field separator  | `,` (comma), no spaces                             |

**Auto-reset:** opening the port resets the Uno. The host waits **2 s** after
opening the port before it sends anything.

---

## 2. Messages from the Arduino to the host

| Message | When | Example |
|---|---|---|
| `READY,<firmware>` | Once at boot, and in reply to `HELLO` | `READY,PBR-FW-1.0` |
| `DATA,<temp>,<led>,<mode>,<rtc>` | Every *sampling interval*, and in reply to `GET` | `DATA,24.56,1,A,2026-10-30 14:05:00` |
| `ACK,<command>[,<args>]` | After every valid command | `ACK,SCHED,06:00,18:00` |
| `ERR,<code>[,<detail>]` | Invalid command or hardware fault | `ERR,BAD_ARGS,SCHED,25:00` |
| `PONG` | In reply to `PING` | `PONG` |

### `DATA` fields

| # | Field  | Format | Meaning |
|---|--------|--------|---------|
| 1 | `temp` | decimal °C, 2 dp (e.g. `24.56`) or `NAN` | Culture temperature. Send `NAN` if the sensor read fails. Values ≤ −100 or ≥ 125 (e.g. DS18B20 fault codes −127 / 85) are also treated as a sensor fault. |
| 2 | `led`  | `0` or `1` | **Actual** LED output state |
| 3 | `mode` | `A` or `M` | Lighting mode: `A` = automatic (RTC schedule), `M` = manual |
| 4 | `rtc`  | `YYYY-MM-DD HH:MM:SS` | Current RTC time |

Fields 3 and 4 are optional for very early firmware, but strongly recommended.

### `ERR` codes

| Code | Meaning |
|---|---|
| `UNKNOWN_CMD` | Command not recognised |
| `BAD_ARGS` | Command recognised but arguments invalid |
| `SENSOR` | Temperature sensor not found / failed (host raises a *sensor fault* alert) |
| `RTC` | RTC not found or lost power |

---

## 3. Commands from the host to the Arduino

| Command | Effect | Reply |
|---|---|---|
| `PING` | Liveness check | `PONG` |
| `HELLO` | Identify firmware | `READY,<firmware>` |
| `GET` | Send a reading now | `DATA,...` |
| `TIME,YYYY-MM-DD HH:MM:SS` | Set the RTC to the PC clock | `ACK,TIME` |
| `INTERVAL,<s>` | Set the `DATA` reporting period in seconds (1–3600) | `ACK,INTERVAL,<s>` |
| `SCHED,HH:MM,HH:MM` | Set the LED start (ON) and stop (OFF) time, 24 h | `ACK,SCHED,HH:MM,HH:MM` |
| `MODE,AUTO` | Follow the RTC schedule | `ACK,MODE,AUTO` |
| `MODE,MANUAL` | Stop following the schedule; keep the current LED state | `ACK,MODE,MANUAL` |
| `LED,ON` / `LED,OFF` | Switch the LED; **also switches mode to MANUAL** | `ACK,LED,ON` / `ACK,LED,OFF` |

Commands are case-insensitive. After `LED`/`MODE` commands, the firmware should
send a `DATA` line straight away so the dashboard updates at once (recommended).

### Schedule rules (firmware side)

* LED is ON when `start ≤ now < stop`.
* Overnight windows are supported: `SCHED,20:00,08:00` means ON from 20:00 to 08:00.
* `start == stop` means the LED is always OFF in AUTO mode.
* Store the schedule and mode in EEPROM so that the reactor keeps its light cycle
  if the PC is switched off. The RTC is what keeps the photoperiod running on its own.

---

## 4. Connection sequence

```
Host                                   Arduino
 | open port (Uno resets)                |
 | ... wait 2 s ...                      |
 |                         READY,PBR-FW-1.0  (boot message, may be missed)
 | HELLO  ------------------------------>|
 |<------------------------------ READY,PBR-FW-1.0
 | TIME,2026-10-30 14:05:00 ------------>|
 |<------------------------------ ACK,TIME
 | INTERVAL,2 -------------------------->|
 |<------------------------------ ACK,INTERVAL,2
 | SCHED,06:00,18:00 ------------------->|
 |<------------------------------ ACK,SCHED,06:00,18:00
 | MODE,AUTO --------------------------->|
 |<------------------------------ ACK,MODE,AUTO
 | GET --------------------------------->|
 |<------------------------------ DATA,24.56,1,A,2026-10-30 14:05:00
 |<------------------------------ DATA,24.58,1,A,2026-10-30 14:05:02   (every 2 s)
```

The host re-sends `INTERVAL`, `SCHED` and `MODE` whenever these are changed in
the web interface.

## 5. Host behaviour (for reference)

* **Connected** = at least one valid line has been received.
* **Connection lost** = no line for `max(10 s, 3 × sampling interval)`. The host
  raises an alert, closes the port and retries every 3–5 s.
* Every `DATA` line goes to the live view. A reading is saved to the database
  every *recording interval*.
* An LED change between two `DATA` lines is saved as a lighting event. It is
  marked `schedule` in AUTO mode and `manual` in MANUAL mode.
* Temperature outside the configured limits raises an alert. A 0.2 °C
  hysteresis stops repeated alerts near a limit.
* All records are tagged `source = hardware` (or `simulated` in simulation mode).

## 6. Testing the firmware without the web app

Open the Arduino IDE Serial Monitor at 9600 baud with the line ending set to
"Newline", then type:

```
PING          -> PONG
GET           -> DATA,24.56,0,A,2026-10-30 14:05:00
LED,ON        -> ACK,LED,ON   then DATA,...,1,M,...
MODE,AUTO     -> ACK,MODE,AUTO
SCHED,07:00,19:00 -> ACK,SCHED,07:00,19:00
```

You can type the same commands in the web app at **Settings → Serial console**.
