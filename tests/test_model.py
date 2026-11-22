import json
from pathlib import Path

from app.model import circuit_id, normalise_outputs, offline_circuits, slot_status, summarise

FIX = Path(__file__).parent / "fixtures"
OUTPUTS = json.loads((FIX / "outputs_205.json").read_text())


def test_circuit_id():
    assert circuit_id(12, 18) == "1218"
    assert circuit_id(11, 1) == "1101"
    assert circuit_id(None, 5) is None
    assert circuit_id(12, 0) is None


def test_slot_status_rules():
    assert slot_status({"type": "None", "breakerStatus": "tripped"}) == "empty"
    assert slot_status({"type": "1-Pole Breaker", "breakerStatus": "tripped", "outputStatus": "on"}) == "tripped"
    assert slot_status({"type": "1-Pole Breaker", "breakerStatus": "closed", "outputStatus": "on"}) == "on"
    assert slot_status({"type": "1-Pole Breaker", "breakerStatus": "closed", "outputStatus": "off"}) == "off"


def test_fixture_counts_and_units():
    cs = normalise_outputs(OUTPUTS, panel_no=12)
    assert len(cs) == 48
    s = summarise(cs)
    assert s["counts"]["on"] == 30
    assert s["counts"]["empty"] == 4
    assert s["counts"]["tripped"] + s["counts"]["empty"] + s["counts"]["on"] + s["counts"]["off"] == 48
    slot16 = next(c for c in cs if c.slot == 16)
    assert slot16.current_a == 11.569
    assert slot16.circuit == "1216"
    assert 1440 < slot16.power_w < 1450


def test_slot_offset():
    cs = normalise_outputs(OUTPUTS, panel_no=12, slot_offset=24)
    assert cs[0].circuit == "1225"


def test_two_pole_marks_second_slot():
    raw = [{"slot": 1, "type": "2-Pole Breaker", "poleCount": 2, "outputStatus": "on", "breakerStatus": "closed",
            "load": {"current": 1000, "power": 208000, "voltage": 208}},
           {"slot": 2, "type": "2-Pole Breaker", "poleCount": 2, "outputStatus": "on", "breakerStatus": "closed",
            "load": {}}]
    cs = normalise_outputs(raw, 11)
    assert cs[0].pole_of is None and cs[1].pole_of == 1


def test_offline_keeps_identity():
    cs = normalise_outputs(OUTPUTS, panel_no=12)
    off = offline_circuits(cs, 12, 0)
    assert all(c.status == "offline" and c.current_a == 0 for c in off)
    assert [c.circuit for c in off] == [c.circuit for c in cs]
    assert len(offline_circuits([], 12, 0)) == 48
