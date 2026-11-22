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
