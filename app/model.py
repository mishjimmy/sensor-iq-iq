"""Normalise raw SensorIQ slot data into circuit status records and rollups."""
from dataclasses import asdict, dataclass, field
from typing import Any

STATUSES = ("on", "off", "tripped", "empty", "offline")


def circuit_id(panel_no: int | None, circuit_no: int) -> str | None:
    """Grist join key: 'PPCC', e.g. panel 12 circuit 18 -> '1218'."""
    if panel_no is None or circuit_no < 1:
        return None
    return f"{panel_no:02d}{circuit_no:02d}"


@dataclass
class Circuit:
    slot: int
    circuit_no: int
    circuit: str | None  # Grist key
    status: str
    current_a: float = 0.0
    power_w: float = 0.0
    voltage: float = 0.0
    breaker_type: str = ""
    pole_of: int | None = None  # set on the 2nd+ slot of a multi-pole breaker
    errors: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)  # panel's /api/outputs entry

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw")  # kept out of the dashboard snapshot; only sent to Grist
        return d


def slot_status(raw: dict[str, Any]) -> str:
    if (raw.get("type") or "None") == "None":
        return "empty"
    if raw.get("breakerStatus") == "tripped":
        return "tripped"
    if raw.get("outputStatus") == "on":
        return "on"
    return "off"


def normalise_outputs(outputs: list[dict[str, Any]], panel_no: int | None, slot_offset: int = 0) -> list[Circuit]:
    circuits: list[Circuit] = []
    pole_owner: dict[int, int] = {}  # slot -> first slot of its breaker
    for raw in sorted(outputs, key=lambda r: r.get("slot", 0)):
        slot = int(raw.get("slot", 0))
        load = raw.get("load") or {}
        cno = slot + slot_offset
        c = Circuit(
            slot=slot,
            circuit_no=cno,
            circuit=circuit_id(panel_no, cno),
            status=slot_status(raw),
            current_a=round((load.get("current") or 0) / 1000, 3),
            power_w=round((load.get("power") or 0) / 1000, 1),
            voltage=float(load.get("voltage") or 0),
            breaker_type=raw.get("type") or "",
            errors=[e.get("errorText", "") for e in raw.get("errors") or []],
            raw=raw,
        )
        poles = int(raw.get("poleCount") or 1)
        for extra in range(1, poles):
            pole_owner[slot + extra] = slot
        if slot in pole_owner:
            c.pole_of = pole_owner[slot]
        circuits.append(c)
    return circuits


def offline_circuits(previous: list[Circuit], panel_no: int | None, slot_offset: int, slot_count: int = 48) -> list[Circuit]:
    """Circuits to report when a panel is unreachable: keep identity, drop readings."""
    if previous:
        return [Circuit(slot=c.slot, circuit_no=c.circuit_no, circuit=c.circuit, status="offline",
                        breaker_type=c.breaker_type, pole_of=c.pole_of) for c in previous]
    return [Circuit(slot=s, circuit_no=s + slot_offset, circuit=circuit_id(panel_no, s + slot_offset), status="offline")
            for s in range(1, slot_count + 1)]


def summarise(circuits: list[Circuit]) -> dict[str, Any]:
    counts = {s: 0 for s in STATUSES}
    for c in circuits:
        counts[c.status] += 1
    return {
        "counts": counts,
        "total_a": round(sum(c.current_a for c in circuits), 2),
        "total_w": round(sum(c.power_w for c in circuits), 1),
    }
