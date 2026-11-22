"""The panel client must never be able to change panel state."""
import ast
from pathlib import Path

import httpx
import pytest

from app import sensoriq

SRC = Path(sensoriq.__file__).read_text()


def test_only_get_calls_in_source():
    tree = ast.parse(SRC)
    methods = {n.func.attr for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not methods & {"post", "put", "patch", "delete", "request", "send", "stream"}
    assert "get" in methods


@pytest.mark.asyncio
async def test_allowlist_rejects_other_paths():
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        for bad in ["/api/outputs/1", "/api/presets", "/", "/api/info?x=1", "/api/../api/info"]:
            with pytest.raises(sensoriq.ReadOnlyViolation):
                await sensoriq._get(c, "10.0.0.1", bad, 1)
        await sensoriq.get_info(c, "10.0.0.1")
        await sensoriq.get_outputs(c, "10.0.0.1")
    assert seen == [("GET", "/api/info"), ("GET", "/api/outputs")]
