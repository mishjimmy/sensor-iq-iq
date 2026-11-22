from app.history import History, Sample, series_bucket
from app.model import Circuit

T0 = 1_800_000_000 - (1_800_000_000 % 3600)  # aligned to an hour


def circuits(status3="on", a1=10.0):
    return [
        Circuit(slot=1, circuit_no=1, circuit="1201", status="on", current_a=a1, power_w=a1 * 120),
        Circuit(slot=2, circuit_no=2, circuit="1202", status="off"),
        Circuit(slot=3, circuit_no=3, circuit="1203", status=status3, current_a=2.0 if status3 == "on" else 0,
                power_w=240 if status3 == "on" else 0),
        Circuit(slot=4, circuit_no=4, circuit="1204", status="empty"),
    ]


def test_series_bucket():
    assert series_bucket(6 * 3600) == 60
    assert series_bucket(86400) == 120
    assert series_bucket(30 * 86400) == 3600


def test_record_and_query(tmp_path):
    h = History(tmp_path / "h.db")
    # 2 hours of 5-second polls; circuit 3 trips at +1h, panel drops offline for the last 10 minutes
    for i in range(0, 7200, 5):
        t = T0 + i
        online = i < 6600
        tripped = i >= 3600
        h.record([Sample("10.0.0.12", "P12", "EER01", online, circuits("tripped" if tripped else "on"))], now=t)
    h.flush(force=True)
    q = h.query("6h", now=T0 + 7200)

    k = q["kpis"]
    # first hour 1440 W, second hour (online part) 1200 W -> 1.44 kWh + 1200*50/60/1000 = 2.44 kWh
    assert abs(k["energy_kwh"] - 2.44) < 0.01
    assert k["peak_w"] == 1440
    assert k["trips"] == 1
    assert q["series"][0][1] == 1440 and q["series"][-1][1] == 1200

    by_slot = {c["slot"]: c for c in q["circuits"]}
    assert 2 not in by_slot and 4 not in by_slot          # never drew current / empty -> not stored
    assert by_slot[1]["max_a"] == 10 and abs(by_slot[1]["avg_a"] - 10) < 0.01
    assert abs(by_slot[3]["on_hours"] - 1.0) < 0.01
    assert by_slot[1]["peak_w"] == 1200 and by_slot[1]["on_w"] == 1200
    assert abs(by_slot[3]["on_w"] - 240) < 0.5             # averaged only over time it was on

    kinds = {(e["slot"], e["old"], e["new"]) for e in q["events"]}
    assert (3, "on", "tripped") in kinds
    assert (0, "online", "offline") in kinds

    hours = {r[0]: r for r in q["hourly"]}
    assert hours[T0][2] == 1440 and hours[T0][3] == 60
    assert hours[T0 + 3600][3] == 50                       # 10 offline minutes not counted
    assert q["panels"]["10.0.0.12"] == {"name": "P12", "room": "EER01"}
    h.close()


def test_retention(tmp_path):
    h = History(tmp_path / "h.db", retention_days=1)
    h.record([Sample("ip", "P", "", True, circuits())], now=T0)
    h.record([Sample("ip", "P", "", True, circuits())], now=T0 + 600)
    h.flush(force=True)
    h._prune(T0 + 2 * 86400)
    assert h.query("30d", now=T0 + 2 * 86400)["series"] == []
    h.close()
