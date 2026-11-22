import json

import httpx
import pytest

from app.grist import GristClient, circuit_fields, split_rooms
from app.model import Circuit


def test_split_rooms():
    assert split_rooms("TLB-01, TLB-10") == ["TLB-01", "TLB-10"]
    assert split_rooms("") == [] and split_rooms(None) == []


@pytest.mark.asyncio
async def test_upsert_shape_and_change_detection():
    bodies = []

    def handler(req: httpx.Request):
        assert req.headers["Authorization"] == "Bearer k"
        if req.method == "GET":
            assert req.url.path == "/api/docs/D/sql"
            return httpx.Response(200, json={"records": [
                {"fields": {"Circuit": "1201", "Panel": 12, "Circuit_No": 1, "Rooms": "A, B",
                            "Load_Types": "X", "Fixture_Count": 3}}]})
        assert req.method == "PUT" and req.url.path == "/api/docs/D/tables/Panel_Circuit_Live/records"
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        g = GristClient("https://g.example/", "D", "k", c)
        m = await g.fetch_map()
        assert m["1201"]["rooms"] == ["A", "B"]

        c1 = Circuit(slot=1, circuit_no=1, circuit="1201", status="on", current_a=0.473, power_w=59.2, voltage=125)
        rows = {"1201": circuit_fields(c1, 12, "P12")}
        assert await g.push_circuits(rows) == 1
        rec = bodies[0]["records"][0]
        assert rec["require"] == {"Circuit": "1201"}
        assert rec["fields"]["Status"] == "on" and rec["fields"]["Current_A"] == 0.5
        assert "Updated_At" in rec["fields"]

        assert await g.push_circuits(rows) == 0          # unchanged -> no request
        c1.current_a = 0.49                               # rounds to same 0.5 A
        assert await g.push_circuits({"1201": circuit_fields(c1, 12, "P12")}) == 0
        c1.status = "tripped"
        assert await g.push_circuits({"1201": circuit_fields(c1, 12, "P12")}) == 1
        assert len(bodies) == 2


@pytest.mark.asyncio
async def test_raw_json_sent_but_load_jitter_ignored():
    from pathlib import Path
    from app.model import normalise_outputs
    outputs = json.loads((Path(__file__).parent / "fixtures" / "outputs_205.json").read_text())
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        g = GristClient("https://g.example", "D", "k", c)
        rows = lambda outs: {x.circuit: circuit_fields(x, 12, "P12") for x in normalise_outputs(outs, 12)}
        assert await g.push_circuits(rows(outputs)) == 48
        raw16 = json.loads(next(r for r in bodies[0]["records"] if r["require"]["Circuit"] == "1216")["fields"]["Raw"])
        assert raw16["slot"] == 16 and "arbitrationTable" in raw16 and "patch" in raw16 and "load" in raw16

        # a few mA of jitter on every slot (same 0.1 A rounding) -> nothing re-sent
        jitter = json.loads(json.dumps(outputs))
        for o in jitter:
            o["load"]["current"] += 7 if o["load"]["current"] else 0
            o["load"]["power"] += 800 if o["load"]["power"] else 0
        assert await g.push_circuits(rows(jitter)) == 0

        # a patch change is meaningful even though status/current are unchanged
        jitter[2]["patch"]["dmxAddr"] = 400
        assert await g.push_circuits(rows(jitter)) == 1
    assert "raw" not in normalise_outputs(outputs, 12)[0].to_dict()      # kept out of the dashboard SSE
