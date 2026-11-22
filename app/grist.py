"""Grist REST client: read the circuit map, upsert live status.

Only writes to the two tables this service owns (Panel_Circuit_Live, Panel_Live).
"""
import logging
import time
from typing import Any

import httpx

from .model import Circuit

log = logging.getLogger(__name__)

CIRCUIT_TABLE = "Panel_Circuit_Live"
PANEL_TABLE = "Panel_Live"
MAP_SQL = ("SELECT Circuit, Panel, Circuit_No, Rooms, Load_Types, Fixture_Count "
           "FROM Panel_Schedule_Export")
BATCH = 500


def split_rooms(value: Any) -> list[str]:
    return [r.strip() for r in str(value or "").split(",") if r.strip()]


def circuit_fields(c: Circuit, panel_no: int, panel_name: str) -> dict[str, Any]:
    """Fields written for one circuit (Updated_At is added at send time)."""
    return {
        "Panel_No": panel_no,
        "Circuit_No": c.circuit_no,
        "Status": c.status,
        "Current_A": round(c.current_a, 1),
        "Power_W": round(c.power_w),
        "Voltage": round(c.voltage),
        "Panel_Name": panel_name,
    }


class GristClient:
    def __init__(self, server: str, doc_id: str, api_key: str, client: httpx.AsyncClient):
        self.base = f"{server.rstrip('/')}/api/docs/{doc_id}"
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.client = client
        self._sent: dict[str, dict[str, Any]] = {}  # Circuit -> last fields sent

    async def fetch_map(self) -> dict[str, dict[str, Any]]:
        r = await self.client.get(f"{self.base}/sql", params={"q": MAP_SQL}, headers=self.headers, timeout=30)
        r.raise_for_status()
        out: dict[str, dict[str, Any]] = {}
        for rec in r.json().get("records", []):
            f = rec["fields"]
            key = str(f.get("Circuit") or "").strip()
            if not key:
                continue
            out[key] = {
                "panel": f.get("Panel"),
                "circuit_no": f.get("Circuit_No"),
                "rooms": split_rooms(f.get("Rooms")),
                "load_types": f.get("Load_Types") or "",
                "fixture_count": f.get("Fixture_Count") or 0,
            }
        return out

    def changed_circuits(self, rows: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {k: v for k, v in rows.items() if self._sent.get(k) != v}

    async def push_circuits(self, rows: dict[str, dict[str, Any]]) -> int:
        """Upsert only rows whose fields changed since the last successful push."""
        changed = self.changed_circuits(rows)
        if not changed:
            return 0
        now = time.time()
        items = list(changed.items())
        for i in range(0, len(items), BATCH):
            chunk = items[i:i + BATCH]
            body = {"records": [{"require": {"Circuit": k}, "fields": {**v, "Updated_At": now}} for k, v in chunk]}
            await self._put(CIRCUIT_TABLE, body)
            self._sent.update({k: dict(v) for k, v in chunk})
        return len(changed)

    async def push_panels(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        body = {"records": [{"require": {"Panel_No": r["Panel_No"]},
                             "fields": {k: v for k, v in r.items() if k != "Panel_No"}} for r in rows]}
        await self._put(PANEL_TABLE, body)

    async def _put(self, table: str, body: dict[str, Any]) -> None:
        r = await self.client.put(f"{self.base}/tables/{table}/records", json=body,
                                  headers=self.headers, timeout=30)
        if r.status_code >= 400:
            raise httpx.HTTPStatusError(f"Grist {table} upsert failed: {r.status_code} {r.text[:300]}",
                                        request=r.request, response=r)
