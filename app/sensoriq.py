"""Read-only client for ETC SensorIQ panels.

This module must never change panel state. Every request goes through
``_get``, which only issues HTTP GET and only to an allowlisted path.
"""
from typing import Any

import httpx

ALLOWED_PATHS = frozenset({"/api/info", "/api/outputs", "/api/config"})


class ReadOnlyViolation(ValueError):
    pass


async def _get(client: httpx.AsyncClient, ip: str, path: str, timeout: float) -> httpx.Response:
    if path not in ALLOWED_PATHS:
        raise ReadOnlyViolation(f"path not allowed: {path!r}")
    resp = await client.get(f"http://{ip}{path}", timeout=timeout)
    resp.raise_for_status()
    return resp


async def get_info(client: httpx.AsyncClient, ip: str, timeout: float = 3) -> dict[str, Any]:
    return (await _get(client, ip, "/api/info", timeout)).json()


async def get_outputs(client: httpx.AsyncClient, ip: str, timeout: float = 3) -> list[dict[str, Any]]:
    return (await _get(client, ip, "/api/outputs", timeout)).json()


async def get_config(client: httpx.AsyncClient, ip: str, timeout: float = 15) -> str:
    """Full panel config as XML text. Only fetched on demand, never polled."""
    return (await _get(client, ip, "/api/config", timeout)).text
