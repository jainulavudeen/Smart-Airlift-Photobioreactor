"""Device communication for the Photobioreactor monitor.

Two interchangeable "links" speak the same line-based serial protocol
(see docs/SERIAL_PROTOCOL.md):

* SimulatedLink - a virtual Arduino implemented in Python, used for testing.
* SerialLink    - the real Arduino Uno over USB (PySerial).

DeviceManager owns whichever link is active, parses the protocol, records data,
raises alerts and pushes live updates to the browser via Flask-SocketIO.
Because the simulator speaks the real protocol, the full parsing / recording
path is exercised even before the hardware exists.

NOTE: temperature is MONITORED ONLY. Nothing here drives a heater or chiller.
"""
import math
import queue
import random
import threading
import time
from collections import deque
from datetime import datetime

import database as db

FIRMWARE_SIM = "SIM-1.0"
HYSTERESIS = 0.2  # degC, avoids alert chatter around a limit


def parse_hhmm(s):
    h, m = s.split(":")
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("time out of range")
    return h * 60 + m


def schedule_is_on(minute_of_day, start_min, stop_min):
    """Light is ON in [start, stop). Supports overnight windows (e.g. 20:00-08:00)."""
    if start_min == stop_min:
        return False
    if start_min < stop_min:
        return start_min <= minute_of_day < stop_min
    return minute_of_day >= start_min or minute_of_day < stop_min


# =========================================================================
# Links
# =========================================================================
class SimulatedLink:
    """A virtual Arduino. Accepts protocol commands, emits protocol lines."""

    source = "simulated"

    def __init__(self):
        self._out = queue.Queue()
        self._lock = threading.Lock()
        self.interval = 2.0
        self.mode_auto = True
        self.manual_led = False
        self.sched = (6 * 60, 18 * 60)
        self.clock_offset = 0.0           # simulated RTC offset vs. PC clock
        self.temp = 24.5 + random.uniform(-0.5, 0.5)
        self.excursion = 0.0              # occasional disturbance to exercise alerts
        self.excursion_left = 0
        self.next_report = time.time() + 0.5
        self._out.put(f"READY,{FIRMWARE_SIM}")

    # --- link interface
    def open(self):
        pass

    def close(self):
        pass

    def write_line(self, line):
        parts = line.strip().split(",")
        cmd = parts[0].upper()
        with self._lock:
            try:
                if cmd == "PING":
                    self._out.put("PONG")
                elif cmd == "HELLO":
                    self._out.put(f"READY,{FIRMWARE_SIM}")
                elif cmd == "GET":
                    self._out.put(self._data_line())
                elif cmd == "LED" and parts[1].upper() in ("ON", "OFF"):
                    self.manual_led = parts[1].upper() == "ON"
                    self.mode_auto = False
                    self._out.put(f"ACK,LED,{parts[1].upper()}")
                    self._out.put(self._data_line())
                elif cmd == "MODE" and parts[1].upper() in ("AUTO", "MANUAL"):
                    if parts[1].upper() == "MANUAL" and self.mode_auto:
                        self.manual_led = self._led_on()  # hold current state
                    self.mode_auto = parts[1].upper() == "AUTO"
                    self._out.put(f"ACK,MODE,{parts[1].upper()}")
                    self._out.put(self._data_line())
                elif cmd == "SCHED":
                    self.sched = (parse_hhmm(parts[1]), parse_hhmm(parts[2]))
                    self._out.put(f"ACK,SCHED,{parts[1]},{parts[2]}")
                elif cmd == "INTERVAL":
                    self.interval = max(1.0, float(parts[1]))
                    self._out.put(f"ACK,INTERVAL,{int(self.interval)}")
                elif cmd == "TIME":
                    rtc = datetime.strptime(parts[1], "%Y-%m-%d %H:%M:%S").timestamp()
                    self.clock_offset = rtc - time.time()
                    self._out.put("ACK,TIME")
                else:
                    self._out.put(f"ERR,UNKNOWN_CMD,{line.strip()}")
            except (IndexError, ValueError):
                self._out.put(f"ERR,BAD_ARGS,{line.strip()}")

    def read_line(self, timeout=1.0):
        deadline = time.time() + timeout
        while True:
            try:
                return self._out.get_nowait()
            except queue.Empty:
                pass
            now = time.time()
            if now >= self.next_report:
                self.next_report = now + self.interval
                with self._lock:
                    return self._data_line(step=True)
            if now >= deadline:
                return None
            time.sleep(min(0.1, deadline - now, max(0.01, self.next_report - now)))

    # --- virtual hardware
    def _rtc(self):
        return datetime.fromtimestamp(time.time() + self.clock_offset)

    def _led_on(self):
        if not self.mode_auto:
            return self.manual_led
        t = self._rtc()
        return schedule_is_on(t.hour * 60 + t.minute, *self.sched)

    def _data_line(self, step=False):
        led = self._led_on()
        if step:
            self._step_temperature(led)
        rtc = self._rtc().strftime("%Y-%m-%d %H:%M:%S")
        mode = "A" if self.mode_auto else "M"
        return f"DATA,{self.temp:.2f},{int(led)},{mode},{rtc}"

    def _step_temperature(self, led):
        hour = self._rtc().hour + self._rtc().minute / 60
        ambient = 24.0 + 1.2 * math.sin(2 * math.pi * (hour - 9) / 24)
        target = ambient + (1.6 if led else 0.0)
        # rare disturbance (~every 10-15 min) so limit alerts can be tested
        if self.excursion_left <= 0 and random.random() < 0.0025:
            self.excursion = random.choice([7.5, -6.5])
            self.excursion_left = random.randint(40, 90)
        if self.excursion_left > 0:
            self.excursion_left -= 1
            target += self.excursion
        # first-order thermal lag + sensor noise
        self.temp += (target - self.temp) * 0.06 + random.gauss(0, 0.04)


class SerialLink:
    """Real Arduino over USB serial (PySerial)."""

    source = "hardware"

    def __init__(self, port, baud):
        self.port = port
        self.baud = baud
        self.ser = None
        self._buf = b""
        self._wlock = threading.Lock()

    def open(self):
        import serial  # imported lazily so simulation mode needs no PySerial
        self.ser = serial.Serial(self.port, self.baud, timeout=0.2)
        # Opening the port toggles DTR which resets an Uno; wait for the bootloader.
        time.sleep(2.0)
        self.ser.reset_input_buffer()

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def write_line(self, line):
        with self._wlock:
            self.ser.write((line.strip() + "\n").encode("ascii"))
            self.ser.flush()

    def read_line(self, timeout=1.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                text = line.decode("ascii", errors="replace").strip()
                if text:
                    return text
                continue
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                self._buf += chunk
                if len(self._buf) > 4096:  # garbage / wrong baud rate
                    self._buf = b""
        return None


def list_serial_ports():
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [{"device": p.device, "description": p.description or "", "hwid": p.hwid or ""}
            for p in list_ports.comports()]


# =========================================================================
# Manager
# =========================================================================
class DeviceManager:
    def __init__(self, socketio):
        self.sio = socketio
        self.link = None
        self.running = False
        self._reconnect = threading.Event()
        self._lock = threading.Lock()
        self.log = deque(maxlen=200)           # raw serial console
        self.last_record_ts = 0.0
        self._opened_at, self._opened_link = 0.0, None
        self.temp_state = "no_data"            # normal | high | low | sensor_error | no_data
        self.run_id = (db.active_run() or {}).get("run_id")
        self.settings = db.get_settings()
        self.state = {
            "connected": False,
            "source": None,
            "port": None,
            "firmware": None,
            "last_seen": None,
            "connected_since": None,
            "temperature": None,
            "led_state": None,
            "device_led_mode": None,
            "rtc_time": None,
            "error": None,
        }

    # ----------------------------------------------------------- lifecycle
    def start(self):
        self.running = True
        self.sio.start_background_task(self._loop)

    def reconnect(self):
        self._reconnect.set()

    def _open_link(self):
        s = self.settings
        if s["data_mode"] == "serial":
            if not s["serial_port"]:
                raise RuntimeError("No serial port selected (Settings > Hardware Connection)")
            link = SerialLink(s["serial_port"], int(s["baud_rate"]))
        else:
            link = SimulatedLink()
        link.open()
        return link

    def _close_link(self):
        if self.link:
            self.link.close()
        self.link = None
        self.state.update(connected=False, firmware=None, connected_since=None)

    def _loop(self):
        retry_at = 0.0
        last_emit = 0.0
        while self.running:
            if self._reconnect.is_set():
                self._reconnect.clear()
                self._close_link()
                self._emit_status()
                retry_at = 0.0

            if self.link is None:
                if time.time() < retry_at:
                    time.sleep(0.25)
                    continue
                try:
                    self.link = self._open_link()
                    self.state.update(
                        source=self.link.source,
                        port=getattr(self.link, "port", "virtual"),
                        error=None,
                        last_seen=None,
                        temperature=None,
                        led_state=None,
                    )
                    self.temp_state = "no_data"
                    self._log("sys", f"Link opened ({self.link.source})")
                    self._push_config()
                except Exception as e:  # port busy, unplugged, missing pyserial...
                    self.state.update(connected=False, error=str(e),
                                      source="hardware" if self.settings["data_mode"] == "serial" else "simulated")
                    self._log("sys", f"Connect failed: {e}")
                    self.link = None
                    retry_at = time.time() + 5
                    self._emit_status()
                    continue

            try:
                line = self.link.read_line(timeout=0.5)
            except Exception as e:
                self._log("sys", f"Link error: {e}")
                self._connection_lost(str(e))
                retry_at = time.time() + 3
                continue

            if line:
                self._log("rx", line)
                try:
                    self._handle_line(line)
                except Exception as e:
                    self._log("sys", f"Parse error: {e}")

            self._check_stale()
            if time.time() - last_emit > 1.0:
                self._emit_status()
                last_emit = time.time()

    # ----------------------------------------------------------- protocol
    def send(self, line):
        with self._lock:
            if not self.link:
                raise RuntimeError("Device not connected")
            self._log("tx", line)
            self.link.write_line(line)

    def _push_config(self):
        """Synchronise clock, schedule, interval and lighting mode to the device."""
        s = self.settings
        self.send("HELLO")
        self.send("TIME," + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.send(f"INTERVAL,{int(float(s['sample_interval']))}")
        self.send(f"SCHED,{s['led_start']},{s['led_stop']}")
        self.send("MODE,AUTO" if s["led_mode"] == "auto" else "MODE,MANUAL")
        self.send("GET")

    def _handle_line(self, line):
        parts = line.split(",")
        kind = parts[0].upper()
        now = time.time()
        if kind == "DATA":
            self._mark_alive(now)
            self._handle_data(parts, now)
        elif kind == "READY":
            self._mark_alive(now)
            self.state["firmware"] = parts[1] if len(parts) > 1 else "unknown"
        elif kind in ("PONG", "ACK"):
            self._mark_alive(now)
        elif kind == "ERR":
            self._mark_alive(now)
            msg = ",".join(parts[1:])
            if parts[1:2] == ["SENSOR"]:
                self._set_temp_state("sensor_error", None, now)
            else:
                self._log("sys", f"Device error: {msg}")

    def _mark_alive(self, now):
        if not self.state["connected"]:
            self.state["connected"] = True
            self.state["connected_since"] = now
            self.state["error"] = None
            self._emit_status()
        self.state["last_seen"] = now

    def _handle_data(self, parts, now):
        # DATA,<temp|NAN>,<led 0|1>,<mode A|M>,<YYYY-MM-DD HH:MM:SS>
        if len(parts) < 3:
            raise ValueError(f"short DATA line: {parts}")
        raw_t = parts[1].strip().upper()
        temp = None if raw_t in ("NAN", "ERR", "") else float(raw_t)
        if temp is not None and (temp <= -100 or temp >= 125):  # DS18B20 -127 / 85 fault codes
            temp = None
        led = int(parts[2])
        self.state["device_led_mode"] = (
            ("auto" if parts[3].strip().upper() == "A" else "manual") if len(parts) > 3 else None)
        self.state["rtc_time"] = parts[4].strip() if len(parts) > 4 else None

        prev_led = self.state["led_state"]
        self.state["temperature"] = temp
        self.state["led_state"] = led
        source = self.link.source

        if prev_led is not None and prev_led != led:
            trigger = "manual" if self.settings["led_mode"] == "manual" else "schedule"
            db.insert_light_event(now, led, trigger, self.run_id, source)
            self.sio.emit("led_event", {"ts": now, "state": led, "trigger": trigger, "source": source})
        elif prev_led is None:
            # first reading after (re)connect - record the initial state
            db.insert_light_event(now, led, "device", self.run_id, source)

        self._evaluate_temperature(temp, now)

        if now - self.last_record_ts >= float(self.settings["record_interval"]):
            db.insert_reading(now, temp, led, self.run_id, source)
            self.last_record_ts = now

        self.sio.emit("reading", {
            "ts": now, "temperature": temp, "led_state": led,
            "source": source, "temp_state": self.temp_state, "run_id": self.run_id,
        })

    # ----------------------------------------------------------- alerts
    def _evaluate_temperature(self, temp, now):
        if temp is None:
            self._set_temp_state("sensor_error", None, now)
            return
        lo, hi = float(self.settings["temp_min"]), float(self.settings["temp_max"])
        st = self.temp_state
        if temp > hi:
            new = "high"
        elif temp < lo:
            new = "low"
        elif st == "high" and temp > hi - HYSTERESIS:
            new = "high"
        elif st == "low" and temp < lo + HYSTERESIS:
            new = "low"
        else:
            new = "normal"
        self._set_temp_state(new, temp, now)

    def _set_temp_state(self, new, temp, now):
        old = self.temp_state
        self.temp_state = new
        if new == old:
            return
        lo, hi = self.settings["temp_min"], self.settings["temp_max"]
        if new == "high":
            self._alert("critical", "temp_high", f"Temperature {temp:.2f} °C above upper limit ({hi} °C)", temp, now)
        elif new == "low":
            self._alert("critical", "temp_low", f"Temperature {temp:.2f} °C below lower limit ({lo} °C)", temp, now)
        elif new == "sensor_error":
            self._alert("warning", "sensor", "Temperature sensor fault / no valid reading", None, now)
        elif new == "normal" and old in ("high", "low", "sensor_error"):
            self._alert("info", "temp_normal", f"Temperature back within limits ({temp:.2f} °C)", temp, now)

    def _alert(self, level, kind, message, value, now):
        source = self.link.source if self.link else self.state.get("source") or "simulated"
        a = db.insert_alert(now, level, kind, message, value, self.run_id, source)
        self.sio.emit("alert", a)

    # ----------------------------------------------------------- health
    def _check_stale(self):
        if not self.link:
            return
        limit = max(10.0, 3 * float(self.settings["sample_interval"]))
        if not self.state["connected"]:
            # link is open but the device never answered (wrong port / baud / firmware)
            if self._opened_link is not self.link:
                self._opened_at, self._opened_link = time.time(), self.link
            elif time.time() - self._opened_at > limit:
                self._connection_lost(f"Port open but device not responding after {int(limit)} s")
            return
        if time.time() - self.state["last_seen"] > limit:
            self._connection_lost(f"No data from device for {int(limit)} s")

    def _connection_lost(self, reason):
        was_connected = self.state["connected"]
        self._close_link()
        self.state["error"] = reason
        self.temp_state = "no_data"
        if was_connected:
            self._alert("warning", "connection", f"Device connection lost: {reason}", None, time.time())
        self._emit_status()

    # ----------------------------------------------------------- settings / runs
    def apply_settings(self, changed):
        self.settings = db.get_settings()
        if changed & {"data_mode", "serial_port", "baud_rate"}:
            self.reconnect()
            return
        if not self.link:
            return
        if "sample_interval" in changed:
            self.send(f"INTERVAL,{int(float(self.settings['sample_interval']))}")
        if changed & {"led_start", "led_stop"}:
            self.send(f"SCHED,{self.settings['led_start']},{self.settings['led_stop']}")
        if "led_mode" in changed:
            self.send("MODE,AUTO" if self.settings["led_mode"] == "auto" else "MODE,MANUAL")
        if changed & {"temp_min", "temp_max"} and self.state["temperature"] is not None:
            self._evaluate_temperature(self.state["temperature"], time.time())

    def set_led(self, on):
        db.update_settings({"led_mode": "manual"})
        self.settings = db.get_settings()
        self.send("LED,ON" if on else "LED,OFF")

    def set_run(self, run_id):
        self.run_id = run_id
        self.last_record_ts = 0  # record a point immediately at run start/stop

    # ----------------------------------------------------------- output
    def _log(self, direction, text):
        entry = {"ts": time.time(), "dir": direction, "text": text}
        self.log.append(entry)
        self.sio.emit("serial_log", entry)

    def snapshot(self):
        s = self.settings
        run = db.active_run()
        return {
            **self.state,
            "server_time": time.time(),
            "data_mode": s["data_mode"],
            "temp_state": self.temp_state,
            "temp_min": float(s["temp_min"]),
            "temp_max": float(s["temp_max"]),
            "led_mode": s["led_mode"],
            "led_start": s["led_start"],
            "led_stop": s["led_stop"],
            "sample_interval": float(s["sample_interval"]),
            "record_interval": float(s["record_interval"]),
            "reactor_name": s["reactor_name"],
            "culture_organism": s["culture_organism"],
            "active_run": run,
            "unacked_alerts": db.unacked_count(),
        }

    def _emit_status(self):
        self.sio.emit("status", self.snapshot())
