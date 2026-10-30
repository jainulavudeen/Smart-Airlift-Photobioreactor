"""Smart Airlift Photobioreactor - Monitoring & Control web application.

Run:  python app.py   ->  http://localhost:8000   (override with PBR_PORT=xxxx)
"""
import csv
import io
import os
import time
from datetime import datetime

from flask import Flask, Response, jsonify, render_template, request
from flask_socketio import SocketIO

import database as db
from device import DeviceManager, list_serial_ports, parse_hhmm

app = Flask(__name__)
app.config["SECRET_KEY"] = "pbr-monitor"
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

db.init_db()
device = DeviceManager(socketio)

PAGES = [
    ("dashboard", "/", "Dashboard"),
    ("monitoring", "/monitoring", "Live Monitoring"),
    ("lighting", "/lighting", "Lighting Control"),
    ("runs", "/runs", "Culture Runs"),
    ("history", "/history", "Historical Data"),
    ("settings", "/settings", "Settings"),
]


def page(name, title):
    return lambda: render_template(f"{name}.html", page=name, title=title, pages=PAGES)


for _name, _url, _title in PAGES:
    app.add_url_rule(_url, _name, page(_name, _title))


def err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def time_args():
    """Common query filters: ?hours=N or ?start=<epoch>&end=<epoch>, ?run_id=, ?source="""
    a = request.args
    start = float(a["start"]) if a.get("start") else None
    end = float(a["end"]) if a.get("end") else None
    if a.get("hours") and start is None:
        start = time.time() - float(a["hours"]) * 3600
    return dict(start=start, end=end, run_id=a.get("run_id") or None, source=a.get("source") or None)


# ======================================================================== status
@app.get("/api/status")
def api_status():
    return jsonify(device.snapshot())


# ======================================================================== settings
SETTING_RULES = {
    "reactor_name": str, "reactor_volume_l": float, "culture_organism": str, "reactor_location": str,
    "temp_min": float, "temp_max": float,
    "sample_interval": int, "record_interval": int,
    "led_mode": str, "led_start": str, "led_stop": str,
    "data_mode": str, "serial_port": str, "baud_rate": int,
}


def validate_settings(body, current):
    clean = {}
    for k, v in body.items():
        if k not in SETTING_RULES:
            continue
        try:
            v = SETTING_RULES[k](v)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid value for {k}")
        if isinstance(v, str):
            v = v.strip()
        clean[k] = v
    merged = {**current, **clean}
    if float(merged["temp_min"]) >= float(merged["temp_max"]):
        raise ValueError("Lower temperature limit must be below the upper limit")
    if not 1 <= float(merged["sample_interval"]) <= 3600:
        raise ValueError("Sampling interval must be 1-3600 s")
    if not float(merged["sample_interval"]) <= float(merged["record_interval"]) <= 86400:
        raise ValueError("Recording interval must be ≥ sampling interval and ≤ 86400 s")
    if merged["data_mode"] not in ("simulation", "serial"):
        raise ValueError("data_mode must be 'simulation' or 'serial'")
    if merged["led_mode"] not in ("auto", "manual"):
        raise ValueError("led_mode must be 'auto' or 'manual'")
    for k in ("led_start", "led_stop"):
        parse_hhmm(merged[k])  # raises ValueError
    if int(merged["baud_rate"]) not in (9600, 19200, 38400, 57600, 115200):
        raise ValueError("Unsupported baud rate")
    return {k: v for k, v in clean.items() if str(v) != str(current.get(k))}


@app.get("/api/settings")
def api_get_settings():
    return jsonify(db.get_settings())


@app.put("/api/settings")
def api_put_settings():
    try:
        changed = validate_settings(request.get_json(force=True) or {}, db.get_settings())
    except ValueError as e:
        return err(str(e))
    if changed:
        db.update_settings(changed)
        try:
            device.apply_settings(set(changed))
        except Exception as e:
            return jsonify({"ok": True, "changed": list(changed), "warning": f"Saved, but device not updated: {e}"})
    return jsonify({"ok": True, "changed": list(changed), "settings": db.get_settings()})


# ======================================================================== lighting
@app.post("/api/lighting/schedule")
def api_schedule():
    body = request.get_json(force=True) or {}
    return _save({"led_start": body.get("start"), "led_stop": body.get("stop")})


@app.post("/api/lighting/mode")
def api_mode():
    return _save({"led_mode": (request.get_json(force=True) or {}).get("mode")})


def _save(values):
    try:
        changed = validate_settings(values, db.get_settings())
    except (ValueError, AttributeError) as e:
        return err(str(e) or "Invalid input")
    if changed:
        db.update_settings(changed)
        try:
            device.apply_settings(set(changed))
        except Exception as e:
            return jsonify({"ok": True, "warning": f"Saved, but device not updated: {e}"})
    return jsonify({"ok": True})


@app.post("/api/lighting/led")
def api_led():
    state = str((request.get_json(force=True) or {}).get("state", "")).lower()
    if state not in ("on", "off"):
        return err("state must be 'on' or 'off'")
    try:
        device.set_led(state == "on")
    except Exception as e:
        return err(str(e), 503)
    return jsonify({"ok": True, "mode": "manual", "state": state})


@app.get("/api/lighting/events")
def api_light_events():
    return jsonify(db.query_light_events(**time_args(), limit=int(request.args.get("limit", 500))))


# ======================================================================== readings
@app.get("/api/readings")
def api_readings():
    f = time_args()
    return jsonify({
        "readings": db.query_readings(**f, limit=int(request.args.get("limit", 5000))),
        "stats": db.reading_stats(**f),
    })


# ======================================================================== runs
@app.get("/api/runs")
def api_runs():
    return jsonify(db.list_runs())


@app.get("/api/runs/<run_id>")
def api_run(run_id):
    run = db.get_run(run_id)
    return jsonify(run) if run else err("Run not found", 404)


@app.post("/api/runs/start")
def api_run_start():
    if db.active_run():
        return err("A culture run is already active. Stop it before starting a new one.", 409)
    body = request.get_json(force=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return err("Run name is required")
    s = db.get_settings()
    source = "hardware" if s["data_mode"] == "serial" else "simulated"
    run = db.create_run(name, (body.get("organism") or s["culture_organism"]).strip(),
                        (body.get("notes") or "").strip(), source)
    device.set_run(run["run_id"])
    socketio.emit("run_update", run)
    return jsonify({"ok": True, "run": run})


@app.post("/api/runs/<run_id>/stop")
def api_run_stop(run_id):
    run = db.get_run(run_id)
    if not run or run["status"] != "active":
        return err("Run is not active", 404)
    run = db.stop_run(run_id, ((request.get_json(silent=True) or {}).get("notes") or "").strip())
    device.set_run(None)
    socketio.emit("run_update", run)
    return jsonify({"ok": True, "run": run})


# ======================================================================== alerts
@app.get("/api/alerts")
def api_alerts():
    return jsonify(db.query_alerts(**time_args(), unacked_only=request.args.get("unacked") == "1",
                                   limit=int(request.args.get("limit", 200))))


@app.post("/api/alerts/ack")
def api_alerts_ack():
    db.ack_alerts((request.get_json(silent=True) or {}).get("ids"))
    return jsonify({"ok": True, "unacked": db.unacked_count()})


# ======================================================================== hardware
@app.get("/api/serial/ports")
def api_ports():
    return jsonify(list_serial_ports())


@app.post("/api/connection/reconnect")
def api_reconnect():
    device.reconnect()
    return jsonify({"ok": True})


@app.get("/api/serial/log")
def api_serial_log():
    return jsonify(list(device.log))


@app.post("/api/serial/send")
def api_serial_send():
    line = ((request.get_json(force=True) or {}).get("line") or "").strip()
    if not line or len(line) > 64 or not line.isascii():
        return err("Command must be 1-64 ASCII characters")
    try:
        device.send(line)
    except Exception as e:
        return err(str(e), 503)
    return jsonify({"ok": True})


@app.delete("/api/data/<source>")
def api_delete_source(source):
    if source not in ("simulated", "hardware"):
        return err("source must be 'simulated' or 'hardware'")
    return jsonify({"ok": True, "deleted": db.delete_by_source(source)})


# ======================================================================== CSV export
def iso(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""


def csv_response(filename, header, data):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(data)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


def stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


@app.get("/api/export/readings.csv")
def export_readings():
    data = db.query_readings(**time_args(), limit=10_000_000)
    return csv_response(f"temperature_readings_{stamp()}.csv",
                        ["timestamp", "epoch_s", "temperature_c", "led_state", "run_id", "source"],
                        [[iso(r["ts"]), round(r["ts"], 3),
                          "" if r["temperature"] is None else round(r["temperature"], 2),
                          "ON" if r["led_state"] else "OFF", r["run_id"] or "", r["source"]] for r in data])


@app.get("/api/export/lighting.csv")
def export_lighting():
    data = db.query_light_events(**time_args(), limit=10_000_000)[::-1]
    return csv_response(f"lighting_records_{stamp()}.csv",
                        ["timestamp", "epoch_s", "state", "trigger", "run_id", "source"],
                        [[iso(r["ts"]), round(r["ts"], 3), "ON" if r["state"] else "OFF",
                          r["trigger"], r["run_id"] or "", r["source"]] for r in data])


@app.get("/api/export/runs.csv")
def export_runs():
    out = []
    for r in db.list_runs(limit=100000):
        end = r["end_ts"] or time.time()
        st = db.reading_stats(run_id=r["run_id"])
        out.append([r["run_id"], r["name"], r["organism"] or "", iso(r["start_ts"]), iso(r["end_ts"]),
                    round((end - r["start_ts"]) / 3600, 3), r["status"], r["n_readings"],
                    "" if st["tmin"] is None else round(st["tmin"], 2),
                    "" if st["tmax"] is None else round(st["tmax"], 2),
                    "" if st["tavg"] is None else round(st["tavg"], 2),
                    r["source"], r["notes"] or "", r["end_notes"] or ""])
    return csv_response(f"culture_runs_{stamp()}.csv",
                        ["run_id", "name", "organism", "start", "end", "duration_h", "status", "n_readings",
                         "temp_min_c", "temp_max_c", "temp_avg_c", "source", "notes", "end_notes"], out)


@app.get("/api/export/alerts.csv")
def export_alerts():
    data = db.query_alerts(**time_args(), limit=10_000_000)[::-1]
    return csv_response(f"alerts_{stamp()}.csv",
                        ["timestamp", "level", "kind", "message", "value", "run_id", "source", "acknowledged"],
                        [[iso(r["ts"]), r["level"], r["kind"], r["message"],
                          "" if r["value"] is None else round(r["value"], 2),
                          r["run_id"] or "", r["source"], r["acknowledged"]] for r in data])


# ======================================================================== socket
@socketio.on("connect")
def on_connect():
    socketio.emit("status", device.snapshot(), to=request.sid)


if __name__ == "__main__":
    port = int(os.environ.get("PBR_PORT", 8000))  # 5000 is taken by AirPlay on macOS
    device.start()
    print(f"Photobioreactor monitor running on http://localhost:{port}")
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
