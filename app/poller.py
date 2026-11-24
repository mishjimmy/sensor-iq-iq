"""Background tasks: poll panels, keep in-memory state, sync Grist, notify listeners."""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import sensoriq
from .grist import GristClient, circuit_fields
from .history import History, Sample
from .model import Circuit, normalise_outputs, offline_circuits, summarise
from .registry import Panel, Registry
from .settings import Settings

log = logging.getLogger(__name__)


@dataclass
class PanelState:
    panel: Panel
    online: bool = False
    polled: bool = False
    failures: int = 0
    last_ok: float | None = None
    last_error: str | None = None
    info: dict[str, Any] = field(default_factory=dict)
    circuits: list[Circuit] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "panel": self.panel.to_dict(),
            "online": self.online,
            "polled": self.polled,
            "failures": self.failures,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "info": self.info,
            "summary": summarise(self.circuits),
            "circuits": [c.to_dict() for c in self.circuits],
        }


def info_summary(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "device_name": info.get("name"),
        "model": info.get("model"),
        "slot_count": info.get("slotCount"),
        "uptime_s": info.get("uptime"),
        "incoming_voltage": info.get("incomingVoltage"),
        "errors": [e.get("errorText", "") for e in info.get("currentErrors") or []],
        "mac": (info.get("network") or {}).get("macAddress"),
        "firmware": next((v.get("version") for v in info.get("versionNumbers") or []
                          if v.get("itemName") == "Application"), None),
    }


class Monitor:
    def __init__(self, settings: Settings, registry: Registry, history: History | None = None):
        self.s = settings
        self.registry = registry
        self.history = history
        self.states: dict[str, PanelState] = {}
        self.grist_map: dict[str, dict[str, Any]] = {}
        self.anchor_names: dict[str, str] = {}
        self.grist_status: dict[str, Any] = {"enabled": settings.grist_enabled, "last_sync": None,
                                             "last_map": None, "error": None}
        self.updated_at: float | None = None
        self._listeners: set[asyncio.Queue] = set()
        self._tick = 0
        self.client = httpx.AsyncClient()
        self.grist = (GristClient(settings.grist_server, settings.grist_doc_id, settings.grist_api_key, self.client)
                      if settings.grist_enabled else None)
        self._tasks: list[asyncio.Task] = []
        # (ip, slot) -> (status, since, known); "offline" is never recorded, so a panel outage
        # doesn't count as a circuit change
        self.last_change: dict[tuple[str, int], tuple[str, float, bool]] = (
            history.load_circuit_states() if history else {})
        self._sync_states()

    # ---- lifecycle -----------------------------------------------------
    def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._poll_loop(), name="poll"))
        if self.grist:
            self._tasks.append(asyncio.create_task(self._grist_map_loop(), name="grist-map"))
            self._tasks.append(asyncio.create_task(self._grist_sync_loop(), name="grist-sync"))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.client.aclose()
        if self.history:
            self.history.close()

    # ---- state ---------------------------------------------------------
    def _sync_states(self) -> None:
        """Align panel states with the registry (added / removed / edited panels)."""
        current = {p.ip: p for p in self.registry.panels}
        for ip in list(self.states):
            if ip not in current:
                del self.states[ip]
        for ip, p in current.items():
            st = self.states.get(ip)
            if st is None:
                self.states[ip] = PanelState(panel=p)
            elif st.panel != p:
                remap = (st.panel.grist_panel, st.panel.slot_offset) != (p.grist_panel, p.slot_offset)
                st.panel = p
                if remap:
                    st.circuits = []  # re-derive circuit ids on next poll

    def registry_changed(self) -> None:
        self._sync_states()
        self._notify()

    def snapshot(self) -> dict[str, Any]:
        panels = [st.to_dict() for st in self.states.values()]
        all_circuits = [c for st in self.states.values() for c in st.circuits]
        keys = {c.circuit for c in all_circuits if c.circuit}
        return {
            "updated_at": self.updated_at,
            "summary": summarise(all_circuits),
            "panels": panels,
            "circuit_info": {k: v for k, v in self.grist_map.items() if k in keys},
            "anchor_names": self.anchor_names,
            "grist": self.grist_status,
            "grist_panels": sorted({v["panel"] for v in self.grist_map.values() if v.get("panel")}),
            "registry_error": self.registry.load_error,
            "admin_enabled": bool(self.s.admin_token),
        }

    # ---- listeners (SSE) -----------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    def _notify(self) -> None:
        for q in list(self._listeners):
            if q.full():
                continue  # listener already has a pending "changed" signal
            q.put_nowait(True)

    # ---- polling -------------------------------------------------------
    async def poll_panel(self, st: PanelState) -> None:
        p = st.panel
        try:
            info, outputs = await asyncio.gather(
                sensoriq.get_info(self.client, p.ip, self.s.poll_timeout_s),
                sensoriq.get_outputs(self.client, p.ip, self.s.poll_timeout_s),
            )
        except Exception as e:  # network errors, HTTP errors, bad JSON
            st.failures += 1
            st.last_error = f"{type(e).__name__}: {e}"[:300]
            if st.failures >= self.s.offline_after_failures or not st.polled:
                if st.online or not st.polled:
                    log.warning("panel %s (%s) offline: %s", p.name, p.ip, st.last_error)
                st.online = False
                st.circuits = offline_circuits(st.circuits, p.grist_panel, p.slot_offset,
                                               st.info.get("slot_count") or 48)
                await self._stamp_changes(st)
            st.polled = True
            return
        st.info = info_summary(info)
        st.circuits = normalise_outputs(outputs, p.grist_panel, p.slot_offset)
        await self._stamp_changes(st)
        st.online = True
        st.polled = True
        st.failures = 0
        st.last_error = None
        st.last_ok = time.time()

    async def _stamp_changes(self, st: PanelState) -> None:
        """Set each circuit's changed_at from the last time its status was seen to change."""
        now = time.time()
        changed = []
        for c in st.circuits:
            key = (st.panel.ip, c.slot)
            e = self.last_change.get(key)
            if c.status != "offline" and (e is None or e[0] != c.status):
                # first sighting: the real change happened before we were watching
                e = self.last_change[key] = (c.status, now, e is not None)
                changed.append((*key, *e))
            if e:
                c.changed_at, c.change_known = e[1], e[2]
        if changed and self.history:
            try:
                await asyncio.to_thread(self.history.save_circuit_states, changed)
            except Exception:
                log.exception("saving circuit last-change failed")

    async def poll_once(self) -> None:
        if self.registry.reload_if_changed():
            log.info("panels file changed; reloaded")
        self._sync_states()
        due = []
        for st in self.states.values():
            if not st.panel.enabled:
                continue
            # back off unreachable panels to roughly every 6th tick
            if st.failures >= self.s.offline_after_failures and self._tick % 6:
                continue
            due.append(self.poll_panel(st))
        self._tick += 1
        if due:
            await asyncio.gather(*due)
        self.updated_at = time.time()
        if self.history:
            samples = [Sample(ip=st.panel.ip, name=st.panel.name, room=st.panel.room,
                              online=st.online, circuits=list(st.circuits))
                       for st in self.states.values() if st.panel.enabled and st.polled]
            try:
                await asyncio.to_thread(self.history.record, samples, self.updated_at)
            except Exception:
                log.exception("history write failed")
        self._notify()

    async def _poll_loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self.poll_once()
            except Exception:
                log.exception("poll loop error")
            await asyncio.sleep(max(0.5, self.s.poll_interval_s - (time.monotonic() - started)))

    # ---- Grist ---------------------------------------------------------
    async def refresh_grist_map(self) -> None:
        gmap = await self.grist.fetch_map()
        try:
            est = await self.grist.fetch_estimates()
            for k, v in est.items():
                if k in gmap:
                    gmap[k].update(v)
        except Exception as e:  # estimates are an extra; keep the circuit map even if this fails
            log.warning("grist load estimates failed: %s", e)
        self.grist_map = gmap
        try:
            self.anchor_names = await self.grist.fetch_anchor_names()
        except Exception as e:  # names are cosmetic; keep the circuit map even if this fails
            log.warning("grist anchor names failed: %s", e)
        self.grist_status["last_map"] = time.time()
        self._notify()

    def grist_rows(self) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        circuits: dict[str, dict[str, Any]] = {}
        panels: list[dict[str, Any]] = []
        for st in self.states.values():
            p = st.panel
            if p.grist_panel is None or not p.enabled or not st.polled:
                continue
            for c in st.circuits:
                if c.circuit:
                    circuits[c.circuit] = circuit_fields(c, p.grist_panel, p.name)
            summ = summarise(st.circuits)
            panels.append({
                "Panel_No": p.grist_panel, "Name": p.name, "IP": p.ip, "Room": p.room,
                "Online": st.online, "Last_Seen": st.last_ok,
                "Total_A": summ["total_a"], "Tripped_Count": summ["counts"]["tripped"],
                "Errors": "\n".join(st.info.get("errors") or []) if st.online else (st.last_error or ""),
            })
        return circuits, panels

    async def sync_grist(self) -> int:
        circuits, panels = self.grist_rows()
        n = await self.grist.push_circuits(circuits)
        await self.grist.push_panels(panels)
        self.grist_status["last_sync"] = time.time()
        self.grist_status["error"] = None
        return n

    async def _grist_map_loop(self) -> None:
        while True:
            try:
                await self.refresh_grist_map()
            except Exception as e:
                self.grist_status["error"] = f"map: {e}"[:300]
                log.warning("grist map refresh failed: %s", e)
            await asyncio.sleep(self.s.grist_map_refresh_s)

    async def _grist_sync_loop(self) -> None:
        await asyncio.sleep(min(10, self.s.grist_sync_interval_s))  # let the first polls land
        while True:
            try:
                n = await self.sync_grist()
                if n:
                    log.info("grist: upserted %d circuit rows", n)
            except Exception as e:
                self.grist_status["error"] = f"sync: {e}"[:300]
                log.warning("grist sync failed: %s", e)
            await asyncio.sleep(self.s.grist_sync_interval_s)
