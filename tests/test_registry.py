import pytest

from app.registry import Registry, RegistryError

HAND_WRITTEN = """\
# my comment stays
panels:
  - name: Panel 12   # inline comment stays
    ip: 10.64.41.205
    room: EER01
    grist_panel: 12
"""


def test_creates_default_file(tmp_path):
    r = Registry(tmp_path / "panels.yaml")
    assert r.panels == []
    assert "grist_panel" in (tmp_path / "panels.yaml").read_text()


def test_roundtrip_preserves_comments(tmp_path):
    f = tmp_path / "panels.yaml"
    f.write_text(HAND_WRITTEN)
    r = Registry(f)
    assert r.panels[0].grist_panel == 12 and r.panels[0].enabled
    r.add({"name": "Panel 13", "ip": "10.64.41.206", "room": "EER01", "grist_panel": "13"})
    r.update("10.64.41.205", {"room": "EER01 north"})
    text = f.read_text()
    assert "# my comment stays" in text and "# inline comment stays" in text
    r2 = Registry(f)
    assert [p.ip for p in r2.panels] == ["10.64.41.205", "10.64.41.206"]
    assert r2.panels[0].room == "EER01 north"
    r2.remove("10.64.41.206")
    assert len(Registry(f).panels) == 1


@pytest.mark.parametrize("bad", [
    {"name": "", "ip": "10.0.0.1"},
    {"name": "x", "ip": "not-an-ip"},
    {"name": "x", "ip": "10.0.0.9", "grist_panel": "abc"},
    {"name": "x", "ip": "10.64.41.205"},                 # duplicate ip
    {"name": "x", "ip": "10.0.0.9", "grist_panel": 12},  # duplicate grist panel
])
def test_validation(tmp_path, bad):
    f = tmp_path / "panels.yaml"
    f.write_text(HAND_WRITTEN)
    with pytest.raises(RegistryError):
        Registry(f).add(bad)


def test_bad_hand_edit_keeps_last_good(tmp_path):
    f = tmp_path / "panels.yaml"
    f.write_text(HAND_WRITTEN)
    r = Registry(f)
    f.write_text("panels:\n  - name: x\n    ip: nope\n")
    r.reload()
    assert r.load_error and r.panels[0].ip == "10.64.41.205"
    with pytest.raises(RegistryError):
        r.add({"name": "y", "ip": "10.0.0.2"})
