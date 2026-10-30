"""SQLite persistence layer for the Photobioreactor monitor.

Every operation opens its own short-lived connection, which keeps the module
safe to call from the Flask request threads and the device background thread.
All timestamps are stored as Unix epoch seconds (REAL, UTC).
"""
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime

DB_PATH = "pbr_data.db"

DEFAULT_SETTINGS = {
    # Reactor details
    "reactor_name": "Airlift PBR-01",
    "reactor_volume_l": "2.0",
    "culture_organism": "Chlorella vulgaris",
    "reactor_location": "Lab 1",
    # Temperature limits (monitoring/alerting only - no control)
    "temp_min": "20.0",
    "temp_max": "30.0",
    # Intervals (seconds)
    "sample_interval": "2",     # how often the device reports a reading
    "record_interval": "10",    # how often a reading is written to the database
    # Lighting
    "led_mode": "auto",         # auto = follow schedule, manual = user ON/OFF
    "led_start": "06:00",
    "led_stop": "18:00",
    # Hardware connection
    "data_mode": "simulation",  # simulation | serial
    "serial_port": "",
    "baud_rate": "9600",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    temperature REAL,
    led_state   INTEGER,
    run_id      TEXT,
    source      TEXT NOT NULL            -- 'simulated' | 'hardware'
);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);
CREATE INDEX IF NOT EXISTS idx_readings_run ON readings(run_id);

CREATE TABLE IF NOT EXISTS lighting_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    state     INTEGER NOT NULL,          -- 1 = ON, 0 = OFF
    trigger   TEXT NOT NULL,             -- schedule | manual | device
    run_id    TEXT,
    source    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_light_ts ON lighting_events(ts);

CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    organism   TEXT,
    notes      TEXT,
    start_ts   REAL NOT NULL,
    end_ts     REAL,
    status     TEXT NOT NULL,            -- active | completed
    end_notes  TEXT,
    source     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    level      TEXT NOT NULL,            -- warning | critical | info
    kind       TEXT NOT NULL,            -- temp_high | temp_low | sensor | connection
    message    TEXT NOT NULL,
    value      REAL,
    run_id     TEXT,
    source     TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


# ---------------------------------------------------------------- settings
def get_settings():
    with connect() as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}


def update_settings(values):
    with connect() as c:
        for k, v in values.items():
            c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (k, str(v)))


# ---------------------------------------------------------------- readings
def insert_reading(ts, temperature, led_state, run_id, source):
    with connect() as c:
        c.execute(
            "INSERT INTO readings(ts, temperature, led_state, run_id, source) VALUES (?,?,?,?,?)",
            (ts, temperature, led_state, run_id, source),
        )


def _filters(start=None, end=None, run_id=None, source=None):
    where, args = [], []
    if start is not None:
        where.append("ts >= ?"); args.append(start)
    if end is not None:
        where.append("ts <= ?"); args.append(end)
    if run_id:
        where.append("run_id = ?"); args.append(run_id)
    if source:
        where.append("source = ?"); args.append(source)
    return (" WHERE " + " AND ".join(where)) if where else "", args


def query_readings(start=None, end=None, run_id=None, source=None, limit=5000):
    clause, args = _filters(start, end, run_id, source)
    with connect() as c:
        # newest N, returned oldest-first for charting
        data = rows(c.execute(
            f"SELECT * FROM readings{clause} ORDER BY ts DESC LIMIT ?", args + [limit]))
    return data[::-1]


def reading_stats(start=None, end=None, run_id=None, source=None):
    clause, args = _filters(start, end, run_id, source)
    with connect() as c:
        r = c.execute(
            f"SELECT COUNT(*) n, MIN(temperature) tmin, MAX(temperature) tmax, "
            f"AVG(temperature) tavg FROM readings{clause}", args).fetchone()
    return dict(r)


# ---------------------------------------------------------------- lighting
def insert_light_event(ts, state, trigger, run_id, source):
    with connect() as c:
        c.execute(
            "INSERT INTO lighting_events(ts, state, trigger, run_id, source) VALUES (?,?,?,?,?)",
            (ts, state, trigger, run_id, source),
        )


def query_light_events(start=None, end=None, run_id=None, source=None, limit=500):
    clause, args = _filters(start, end, run_id, source)
    with connect() as c:
        return rows(c.execute(
            f"SELECT * FROM lighting_events{clause} ORDER BY ts DESC LIMIT ?", args + [limit]))


# ---------------------------------------------------------------- runs
def next_run_id(now=None):
    """Unique, human-readable run id: RUN-YYYYMMDD-NNN (sequence resets daily)."""
    day = datetime.fromtimestamp(now or time.time()).strftime("%Y%m%d")
    prefix = f"RUN-{day}-"
    with connect() as c:
        r = c.execute("SELECT run_id FROM runs WHERE run_id LIKE ? ORDER BY run_id DESC LIMIT 1",
                      (prefix + "%",)).fetchone()
    seq = int(r["run_id"].rsplit("-", 1)[1]) + 1 if r else 1
    return f"{prefix}{seq:03d}"


def active_run():
    with connect() as c:
        r = c.execute("SELECT * FROM runs WHERE status = 'active' LIMIT 1").fetchone()
    return dict(r) if r else None


def create_run(name, organism, notes, source):
    now = time.time()
    run_id = next_run_id(now)
    with connect() as c:
        c.execute(
            "INSERT INTO runs(run_id, name, organism, notes, start_ts, status, source) "
            "VALUES (?,?,?,?,?, 'active', ?)",
            (run_id, name, organism, notes, now, source),
        )
    return get_run(run_id)


def stop_run(run_id, end_notes):
    with connect() as c:
        c.execute("UPDATE runs SET status='completed', end_ts=?, end_notes=? "
                  "WHERE run_id=? AND status='active'", (time.time(), end_notes, run_id))
    return get_run(run_id)


def get_run(run_id):
    with connect() as c:
        r = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not r:
        return None
    run = dict(r)
    run.update(reading_stats(run_id=run_id))
    return run


def list_runs(limit=200):
    with connect() as c:
        return rows(c.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM readings WHERE run_id = r.run_id) AS n_readings "
            "FROM runs r ORDER BY start_ts DESC LIMIT ?", (limit,)))


# ---------------------------------------------------------------- alerts
def insert_alert(ts, level, kind, message, value, run_id, source):
    with connect() as c:
        cur = c.execute(
            "INSERT INTO alerts(ts, level, kind, message, value, run_id, source) VALUES (?,?,?,?,?,?,?)",
            (ts, level, kind, message, value, run_id, source),
        )
        aid = cur.lastrowid
        return dict(c.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone())


def query_alerts(start=None, end=None, run_id=None, source=None, unacked_only=False, limit=200):
    clause, args = _filters(start, end, run_id, source)
    if unacked_only:
        clause += (" AND" if clause else " WHERE") + " acknowledged = 0 AND level != 'info'"
    with connect() as c:
        return rows(c.execute(f"SELECT * FROM alerts{clause} ORDER BY ts DESC LIMIT ?", args + [limit]))


def ack_alerts(ids=None):
    with connect() as c:
        if ids:
            c.executemany("UPDATE alerts SET acknowledged=1 WHERE id=?", [(i,) for i in ids])
        else:
            c.execute("UPDATE alerts SET acknowledged=1")


def unacked_count():
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM alerts WHERE acknowledged=0 AND level != 'info'").fetchone()[0]


# ---------------------------------------------------------------- maintenance
def delete_by_source(source):
    """Remove all records of a data source (e.g. purge test 'simulated' data)."""
    with connect() as c:
        counts = {}
        for table in ("readings", "lighting_events", "alerts", "runs"):
            if table == "runs":
                cur = c.execute("DELETE FROM runs WHERE source=? AND status!='active'", (source,))
            else:
                cur = c.execute(f"DELETE FROM {table} WHERE source=?", (source,))
            counts[table] = cur.rowcount
    return counts
