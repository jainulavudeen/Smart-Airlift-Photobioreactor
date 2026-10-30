# Web Application API

Base URL: `http://<host>:8000`. All request and response bodies are JSON unless
noted. Timestamps are **Unix epoch seconds** (UTC, float). Errors return
`{"ok": false, "error": "<message>"}` with an HTTP 4xx/5xx status.

Every stored record has a `source` field: `"hardware"` (measured by the Arduino)
or `"simulated"` (made by the built-in simulator).

---

## Status

### `GET /api/status`
Returns a snapshot of the system. The same object is pushed over Socket.IO as `status` about once per second.

```json
{
  "connected": true, "source": "hardware", "data_mode": "serial",
  "port": "/dev/ttyACM0", "firmware": "PBR-FW-1.0",
  "last_seen": 1793383133.6, "connected_since": 1793383131.1, "error": null,
  "temperature": 24.24, "temp_state": "normal",
  "temp_min": 20.0, "temp_max": 30.0,
  "led_state": 1, "led_mode": "auto", "device_led_mode": "auto",
  "led_start": "06:00", "led_stop": "18:00",
  "rtc_time": "2026-10-30 14:05:00",
  "sample_interval": 2.0, "record_interval": 10.0,
  "reactor_name": "Airlift PBR-01", "culture_organism": "Chlorella vulgaris",
  "active_run": { "run_id": "RUN-20261030-001", "name": "...", "start_ts": 1793383147.6, "...": "..." },
  "unacked_alerts": 0, "server_time": 1793383135.2
}
```
`temp_state` is one of `normal | high | low | sensor_error | no_data`.

---

## Settings

### `GET /api/settings`
Returns all settings as strings.

### `PUT /api/settings`
Partial update. Only the keys you send are changed. The new values take effect at once (the device reconnects if the connection settings change).

| Key | Type / allowed values |
|---|---|
| `reactor_name`, `culture_organism`, `reactor_location` | string |
| `reactor_volume_l` | number (L) |
| `temp_min`, `temp_max` | number (°C), `temp_min < temp_max` |
| `sample_interval` | integer 1–3600 s (device reporting period) |
| `record_interval` | integer s, ≥ `sample_interval` (database logging period) |
| `led_mode` | `auto` \| `manual` |
| `led_start`, `led_stop` | `HH:MM` |
| `data_mode` | `simulation` \| `serial` |
| `serial_port` | e.g. `COM3`, `/dev/ttyACM0` |
| `baud_rate` | 9600, 19200, 38400, 57600, 115200 |

Response: `{"ok": true, "changed": ["temp_max"], "settings": {...}}`

---

## Lighting

| Method & path | Body | Description |
|---|---|---|
| `POST /api/lighting/led` | `{"state": "on" \| "off"}` | Manual ON/OFF (switches to manual mode) |
| `POST /api/lighting/mode` | `{"mode": "auto" \| "manual"}` | Follow the schedule, or hold the current state |
| `POST /api/lighting/schedule` | `{"start": "06:00", "stop": "18:00"}` | Set the photoperiod. The device gets it straight away |
| `GET /api/lighting/events` | – | ON/OFF records, newest first. Filters below |

Lighting event: `{"id", "ts", "state": 0|1, "trigger": "schedule"|"manual"|"device", "run_id", "source"}`

---

## Temperature readings

### `GET /api/readings`
Returns `{"readings": [...oldest→newest], "stats": {"n", "tmin", "tmax", "tavg"}}`.
Reading: `{"id", "ts", "temperature", "led_state", "run_id", "source"}` (`temperature` is `null` on sensor fault).

**Common filters** (also for `/api/lighting/events`, `/api/alerts` and the CSV exports):

| Param | Meaning |
|---|---|
| `hours=N` | last N hours |
| `start=<epoch>` / `end=<epoch>` | explicit range |
| `run_id=RUN-...` | only records from that culture run |
| `source=hardware\|simulated` | only one data source |
| `limit=N` | maximum rows |

---

## Culture runs

| Method & path | Body | Description |
|---|---|---|
| `GET /api/runs` | – | All runs, newest first, with `n_readings` |
| `GET /api/runs/<run_id>` | – | One run with temperature stats |
| `POST /api/runs/start` | `{"name", "organism"?, "notes"?}` | Start a run. **409** if a run is already active |
| `POST /api/runs/<run_id>/stop` | `{"notes"?}` | Stop the active run |

Run IDs look like `RUN-YYYYMMDD-NNN`. They are unique, and the number restarts at 001 each day.
The duration is `end_ts − start_ts`, or `now − start_ts` while the run is active.

---

## Alerts

| Method & path | Body | Description |
|---|---|---|
| `GET /api/alerts` | – | Newest first. `unacked=1` returns only open warning/critical alerts |
| `POST /api/alerts/ack` | `{"ids": [1,2]}` or `{}` (all) | Acknowledge |

Alert: `{"id", "ts", "level": "critical"|"warning"|"info", "kind": "temp_high"|"temp_low"|"temp_normal"|"sensor"|"connection", "message", "value", "run_id", "source", "acknowledged"}`

---

## Hardware

| Method & path | Description |
|---|---|
| `GET /api/serial/ports` | Detected serial ports `[{"device", "description", "hwid"}]` |
| `POST /api/connection/reconnect` | Close and reopen the device link |
| `GET /api/serial/log` | The last 200 raw protocol lines `[{"ts", "dir": "rx"\|"tx"\|"sys", "text"}]` |
| `POST /api/serial/send` | `{"line": "PING"}` sends a raw command (for debugging) |
| `DELETE /api/data/simulated` | Delete all simulated records (hardware data is kept) |

---

## CSV export

`GET /api/export/readings.csv`, `/api/export/lighting.csv`, `/api/export/alerts.csv`
accept the common filters. `GET /api/export/runs.csv` exports all runs with
their duration (hours) and min/max/avg temperature. Every CSV has a `source` column.

---

## Socket.IO events (server → browser)

| Event | Payload | When |
|---|---|---|
| `status` | same as `/api/status` | about every 1 s, and when the connection changes |
| `reading` | `{"ts", "temperature", "led_state", "source", "temp_state", "run_id"}` | every `DATA` line |
| `alert` | alert object | a new alert |
| `led_event` | `{"ts", "state", "trigger", "source"}` | the LED changed |
| `run_update` | run object | a run started or stopped |
| `serial_log` | `{"ts", "dir", "text"}` | every raw line sent or received |
