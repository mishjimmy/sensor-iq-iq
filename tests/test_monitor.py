import json
from pathlib import Path

import httpx
import pytest

from app.poller import Monitor
from app.registry import Registry
from app.settings import Settings

FIX = Path(__file__).parent / "fixtures"
OUTPUTS = json.loads((FIX / "outputs_205.json").read_text())
INFO = json.loads((FIX / "info_205.json").read_text())


def make_monitor(tmp_path, handler):
    f = tmp_path / "panels.yaml"
    f.write_text("panels:\n"
                 "  - {name: P12, ip: 10.0.0.12, room: EER01, grist_panel: 12}\n"
                 "  - {name: Down, ip: 10.0.0.99, room: EER02, grist_panel: 21}\n"
                 "  - {name: Unmapped, ip: 10.0.0.50}\n")
    s = Settings(_env_file=None, panels_file=str(f), offline_after_failures=1)
    m = Monitor(s, Registry(f))
    m.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return m


def panel_handler(req: httpx.Request):
    assert req.method == "GET"
    if req.url.host == "10.0.0.99":
        raise httpx.ConnectTimeout("down")
    return httpx.Response(200, json=INFO if req.url.path == "/api/info" else OUTPUTS)


@pytest.mark.asyncio
async def test_poll_and_grist_rows(tmp_path):
    m = make_monitor(tmp_path, panel_handler)
    await m.poll_once()
    snap = m.snapshot()
    by_name = {p["panel"]["name"]: p for p in snap["panels"]}
    assert by_name["P12"]["online"] and by_name["P12"]["summary"]["counts"]["on"] == 30
    assert not by_name["Down"]["online"] and by_name["Down"]["summary"]["counts"]["offline"] == 48

    circuits, panels = m.grist_rows()
    assert circuits["1216"]["Status"] == "on" and circuits["1216"]["Current_A"] == 11.6
    assert circuits["2101"]["Status"] == "offline"
    assert not any(k.startswith("00") for k in circuits)          # unmapped panel never synced
    assert {p["Panel_No"] for p in panels} == {12, 21}
    assert next(p for p in panels if p["Panel_No"] == 21)["Online"] is False
    await m.client.aclose()


@pytest.mark.asyncio
async def test_registry_edit_remaps_circuits(tmp_path):
    m = make_monitor(tmp_path, panel_handler)
    await m.poll_once()
    m.registry.update("10.0.0.12", {"grist_panel": 13})
    m.registry_changed()
    await m.poll_once()
    circuits, _ = m.grist_rows()
    assert "1316" in circuits and "1216" not in circuits
    await m.client.aclose()
