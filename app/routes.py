"""HTTP API. Panel devices are only ever read; /api/panels edits the local registry file."""
import asyncio
import json
import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from . import sensoriq
from .poller import Monitor, info_summary
from .registry import RegistryError

router = APIRouter(prefix="/api")


def _monitor(request: Request) -> Monitor:
    return request.app.state.monitor


def _require_admin(request: Request, token: str | None) -> None:
    expected = request.app.state.settings.admin_token
    if not expected:
        raise HTTPException(403, "Panel editing from the UI is disabled (set ADMIN_TOKEN). Edit panels.yaml instead.")
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(401, "Invalid admin token")


@router.get("/state")
async def state(request: Request) -> dict[str, Any]:
    return _monitor(request).snapshot()


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    mon = _monitor(request)

    async def events():
        q = mon.subscribe()
        try:
            yield f"data: {json.dumps(mon.snapshot())}\n\n"
            while not await request.is_disconnected():
                try:
                    await asyncio.wait_for(q.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(mon.snapshot())}\n\n"
        finally:
            mon.unsubscribe(q)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/history")
async def history(request: Request, range: str = "24h") -> dict[str, Any]:
    mon = _monitor(request)
    if not mon.history:
        raise HTTPException(503, "history is disabled")
    data = await asyncio.to_thread(mon.history.query, range)
    keys = {c["circuit"] for c in data["circuits"] if c["circuit"]}
    data["circuit_info"] = {k: v for k, v in mon.grist_map.items() if k in keys}
    data["panel_order"] = [p.ip for p in mon.registry.panels]  # stable colour per panel
    return data


# ---- panel registry ------------------------------------------------------
@router.get("/panels")
async def list_panels(request: Request) -> dict[str, Any]:
    mon = _monitor(request)
    return {"panels": [p.to_dict() for p in mon.registry.panels],
            "registry_error": mon.registry.load_error,
            "admin_enabled": bool(request.app.state.settings.admin_token),
            "grist_panels": sorted({v["panel"] for v in mon.grist_map.values() if v.get("panel")})}


@router.post("/panels", status_code=201)
async def add_panel(request: Request, body: dict[str, Any], x_admin_token: str | None = Header(None)):
    _require_admin(request, x_admin_token)
    mon = _monitor(request)
    try:
        panel = mon.registry.add(body)
    except RegistryError as e:
        raise HTTPException(400, str(e)) from None
    mon.registry_changed()
    return panel.to_dict()


@router.put("/panels/{ip}")
async def update_panel(request: Request, ip: str, body: dict[str, Any], x_admin_token: str | None = Header(None)):
    _require_admin(request, x_admin_token)
    mon = _monitor(request)
    try:
        panel = mon.registry.update(ip, body)
    except KeyError:
        raise HTTPException(404, f"no panel {ip}") from None
    except RegistryError as e:
        raise HTTPException(400, str(e)) from None
    mon.registry_changed()
    return panel.to_dict()


@router.delete("/panels/{ip}", status_code=204)
async def delete_panel(request: Request, ip: str, x_admin_token: str | None = Header(None)):
    _require_admin(request, x_admin_token)
    mon = _monitor(request)
    try:
        mon.registry.remove(ip)
    except KeyError:
        raise HTTPException(404, f"no panel {ip}") from None
    except RegistryError as e:
        raise HTTPException(400, str(e)) from None
    mon.registry_changed()


@router.post("/panels/test")
async def test_panel(request: Request, body: dict[str, Any], x_admin_token: str | None = Header(None)):
    """Read /api/info from an IP before adding it (GET only)."""
    _require_admin(request, x_admin_token)
    ip = str(body.get("ip") or "").strip()
    try:
        from .registry import validate
        validate({"name": "test", "ip": ip})
        info = await sensoriq.get_info(_monitor(request).client, ip, 4)
    except RegistryError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        raise HTTPException(502, f"Could not read panel at {ip}: {type(e).__name__}: {e}") from None
    return info_summary(info)


@router.get("/panels/{ip}/config", response_class=PlainTextResponse)
async def panel_config(request: Request, ip: str) -> str:
    mon = _monitor(request)
    if not mon.registry.get(ip):
        raise HTTPException(404, f"no panel {ip}")
    try:
        return await sensoriq.get_config(mon.client, ip)
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}") from None
