"""Usage history in SQLite: 1-minute panel totals, 5-minute circuit stats, status-change events.

Samples are accumulated in memory each poll and flushed when their time bucket closes,
so the database sees one row per panel per minute and one per active circuit per 15 min.
"""
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

PANEL_BUCKET = 60
CIRCUIT_BUCKET = 900

SCHEMA = """
CREATE TABLE IF NOT EXISTS panel_min (
  ts INTEGER NOT NULL, ip TEXT NOT NULL, name TEXT, room TEXT,
  avg_w REAL, max_w REAL, avg_a REAL, samples INTEGER, online_samples INTEGER, tripped INTEGER,
  PRIMARY KEY (ip, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS panel_min_ts ON panel_min(ts);
CREATE TABLE IF NOT EXISTS circuit_15m (
  ts INTEGER NOT NULL, ip TEXT NOT NULL, slot INTEGER NOT NULL, circuit TEXT,
  avg_a REAL, max_a REAL, avg_w REAL, on_frac REAL, samples INTEGER,
  PRIMARY KEY (ip, slot, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS circuit_15m_ts ON circuit_15m(ts);
CREATE TABLE IF NOT EXISTS events (
  ts REAL NOT NULL, ip TEXT NOT NULL, panel_name TEXT, slot INTEGER, circuit TEXT,
  old_status TEXT, new_status TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
"""

RANGES = {"6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
NICE_BUCKETS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800]


def series_bucket(range_s: int, max_points: int = 720) -> int:
    for b in NICE_BUCKETS:
        if range_s / b <= max_points:
            return b
    return NICE_BUCKETS[-1]


@dataclass
class _PanelAcc:
    ts: int
    name: str = ""
    room: str = ""
    sum_w: float = 0.0
    max_w: float = 0.0
    sum_a: float = 0.0
    samples: int = 0
    online: int = 0
    tripped: int = 0


@dataclass
class _CircuitAcc:
    ts: int
    circuit: str | None = None
    sum_a: float = 0.0
    max_a: float = 0.0
    sum_w: float = 0.0
    on: int = 0
    samples: int = 0


@dataclass
class Sample:
    """What the poller hands to History.record for one panel."""
    ip: str
    name: str
    room: str
    online: bool
    circuits: list[Any] = field(default_factory=list)  # model.Circuit


class History:
    def __init__(self, path: str | Path, retention_days: int = 90):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_s = retention_days * 86400
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self._panels: dict[str, _PanelAcc] = {}
        self._circuits: dict[tuple[str, int], _CircuitAcc] = {}
        self._last_status: dict[tuple[str, int], str] = {}
        self._last_online: dict[str, bool] = {}
        self._last_prune = 0.0

    def close(self) -> None:
        self.flush(force=True)
        self._db.close()

    # ---- writing -------------------------------------------------------
    def record(self, samples: Iterable[Sample], now: float | None = None) -> None:
        now = time.time() if now is None else now
        pb = int(now // PANEL_BUCKET) * PANEL_BUCKET
        cb = int(now // CIRCUIT_BUCKET) * CIRCUIT_BUCKET
        events = []
        with self._lock:
            self._flush_closed(pb, cb)
            for s in samples:
                acc = self._panels.get(s.ip)
                if acc is None:
                    acc = self._panels[s.ip] = _PanelAcc(ts=pb)
                acc.name, acc.room = s.name, s.room
                acc.samples += 1
                prev_online = self._last_online.get(s.ip)
                if prev_online is not None and prev_online != s.online:
                    events.append((now, s.ip, s.name, 0, None,
                                   "online" if prev_online else "offline", "online" if s.online else "offline"))
                self._last_online[s.ip] = s.online
                if not s.online:
                    continue
                acc.online += 1
                w = sum(c.power_w for c in s.circuits)
                acc.sum_w += w
                acc.max_w = max(acc.max_w, w)
                acc.sum_a += sum(c.current_a for c in s.circuits)
                acc.tripped = sum(1 for c in s.circuits if c.status == "tripped")
                for c in s.circuits:
                    key = (s.ip, c.slot)
                    prev = self._last_status.get(key)
                    # only trips and recoveries; routine on/off switching would bury them
                    if prev is not None and prev != c.status and "tripped" in (prev, c.status):
                        events.append((now, s.ip, s.name, c.slot, c.circuit, prev, c.status))
                    self._last_status[key] = c.status
                    if c.status == "empty" or c.pole_of:
                        continue
                    ca = self._circuits.get(key)
                    if ca is None:
                        ca = self._circuits[key] = _CircuitAcc(ts=cb)
                    ca.circuit = c.circuit
                    ca.samples += 1
                    ca.sum_a += c.current_a
                    ca.max_a = max(ca.max_a, c.current_a)
                    ca.sum_w += c.power_w
                    ca.on += c.status == "on"
            if events:
                self._db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)", events)
                self._db.commit()
            if now - self._last_prune > 3600:
                self._prune(now)

    def flush(self, force: bool = False) -> None:
        with self._lock:
            if force:
                self._flush_closed(None, None)

    def _flush_closed(self, pb: int | None, cb: int | None) -> None:
        prows, crows = [], []
        for ip, a in list(self._panels.items()):
            if pb is None or a.ts < pb:
                if a.online:
                    prows.append((a.ts, ip, a.name, a.room, a.sum_w / a.online, a.max_w,
                                  a.sum_a / a.online, a.samples, a.online, a.tripped))
                del self._panels[ip]
        for (ip, slot), a in list(self._circuits.items()):
            if cb is None or a.ts < cb:
                # skip circuits that drew nothing and were never on: keeps the table small
                if a.samples and (a.max_a > 0 or a.on):
                    crows.append((a.ts, ip, slot, a.circuit, a.sum_a / a.samples, a.max_a,
                                  a.sum_w / a.samples, a.on / a.samples, a.samples))
                del self._circuits[(ip, slot)]
        if prows:
            self._db.executemany("INSERT OR REPLACE INTO panel_min VALUES (?,?,?,?,?,?,?,?,?,?)", prows)
        if crows:
            self._db.executemany("INSERT OR REPLACE INTO circuit_15m VALUES (?,?,?,?,?,?,?,?,?)", crows)
        if prows or crows:
            self._db.commit()

    def _prune(self, now: float) -> None:
        cutoff = now - self.retention_s
        for table in ("panel_min", "circuit_15m", "events"):
            self._db.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))
        self._db.commit()
        self._last_prune = now

    # ---- reading -------------------------------------------------------
    def query(self, range_key: str, now: float | None = None) -> dict[str, Any]:
        range_s = RANGES.get(range_key, RANGES["24h"])
        now = time.time() if now is None else now
        start = now - range_s
        bucket = series_bucket(range_s)
        with self._lock:
            db = self._db
            first = db.execute("SELECT MIN(ts) FROM panel_min").fetchone()[0]

            # site power per minute (sum across panels), then into display buckets
            per_min = db.execute(
                "SELECT ts, SUM(avg_w), SUM(max_w), SUM(avg_a) FROM panel_min WHERE ts >= ? GROUP BY ts ORDER BY ts",
                (start,)).fetchall()
            buckets: dict[int, list[float]] = {}
            for ts, w, mw, a in per_min:
                b = buckets.setdefault(int(ts // bucket) * bucket, [0.0, 0.0, 0, 0.0])
                b[0] += w; b[1] = max(b[1], mw); b[2] += 1; b[3] += a
            series = [[t, round(v[0] / v[2], 1), round(v[1], 1), round(v[3] / v[2], 2)] for t, v in sorted(buckets.items())]

            site_ws = [w for _, w, _, _ in per_min]
            energy_kwh = sum(site_ws) * PANEL_BUCKET / 3.6e6
            peak = max(per_min, key=lambda r: r[1]) if per_min else None

            hourly = db.execute(
                "SELECT (ts/3600)*3600 AS h, ip, AVG(avg_w), COUNT(*) FROM panel_min WHERE ts >= ? GROUP BY h, ip ORDER BY h",
                (start,)).fetchall()
            # SQLite returns the bare columns from the MAX(ts) row: each panel's latest name/room
            panels = {ip: {"name": n, "room": r} for ip, n, r, _ in db.execute(
                "SELECT ip, name, room, MAX(ts) FROM panel_min WHERE ts >= ? GROUP BY ip", (start,))}
            # per panel: online seconds and online poll samples in range -> seconds per sample
            covered = {ip: (mins * PANEL_BUCKET, n) for ip, mins, n in db.execute(
                "SELECT ip, COUNT(*), SUM(online_samples) FROM panel_min WHERE ts >= ? GROUP BY ip", (start,))}

            circuits = []
            for ip, slot, circuit, sa, max_a, sw, son in db.execute(
                    "SELECT ip, slot, MAX(circuit), SUM(avg_a*samples), MAX(max_a), SUM(avg_w*samples), SUM(on_frac*samples) "
                    "FROM circuit_15m WHERE ts >= ? GROUP BY ip, slot", (start,)):
                cov_s, cov_n = covered.get(ip, (0, 0))
                if not cov_n:
                    continue
                spp = cov_s / cov_n  # seconds represented by one poll sample
                circuits.append({
                    "ip": ip, "slot": slot, "circuit": circuit,
                    "avg_a": round(sa / cov_n, 3),        # averaged over all the time the panel was online
                    "max_a": round(max_a, 2),
                    "kwh": round(sw * spp / 3.6e6, 3),
                    "on_hours": round(son * spp / 3600, 2),
                })

            events = [dict(zip(("ts", "ip", "panel_name", "slot", "circuit", "old", "new"), r)) for r in db.execute(
                "SELECT * FROM events WHERE ts >= ? ORDER BY ts DESC LIMIT 500", (start,))]
            trips = db.execute("SELECT COUNT(*) FROM events WHERE ts >= ? AND new_status = 'tripped'", (start,)).fetchone()[0]

        return {
            "range": range_key if range_key in RANGES else "24h",
            "start": start, "end": now, "bucket_s": bucket,
            "collecting_since": first,
            "kpis": {
                "energy_kwh": round(energy_kwh, 2),
                "avg_w": round(sum(site_ws) / len(site_ws), 1) if site_ws else None,
                "peak_w": round(peak[1], 1) if peak else None,
                "peak_ts": peak[0] if peak else None,
                "trips": trips,
            },
            "series": series,  # [t, avg_w, max_w, avg_a]
            "hourly": [[h, ip, round(w, 1), n] for h, ip, w, n in hourly],  # [hour_ts, ip, avg_w, minutes]
            "panels": panels,
            "circuits": circuits,
            "events": events,
        }
