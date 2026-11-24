from app.report import build_report


def panel(name, ip, online, circuits, enabled=True):
    counts = {s: sum(c["status"] == s for c in circuits) for s in ("on", "off", "tripped")}
    return {"panel": {"name": name, "ip": ip, "enabled": enabled}, "online": online, "last_ok": 1_800_000_000,
            "summary": {"counts": counts}, "circuits": circuits}


def c(slot, status, amps=0.0, circuit=None, pole_of=None):
    return {"slot": slot, "circuit": circuit, "status": status, "current_a": amps, "pole_of": pole_of}


def test_report_is_a_pdf_and_handles_every_panel_state():
    snap = {"panels": [
        panel("Panel 12 MYS", "10.0.0.12", True, [c(1, "on", 2.34, "1201"), c(2, "tripped", 0, "1202"),
                                                   c(3, "empty"), c(4, "on", 1, "1204"), c(5, "on", 0, "1205", pole_of=4)]),
        panel("Panel 31 FOH – ünïcode", "10.0.0.31", False, []),
        panel("Spare", "10.0.0.99", False, [], enabled=False),
    ]}
    pdf = build_report(snap, "America/New_York", now=1_800_000_000)
    assert pdf.startswith(b"%PDF") and len(pdf) > 500
    assert build_report(snap, "Not/AZone").startswith(b"%PDF")  # bad zone falls back to UTC


def test_report_filename():
    from app.report import report_filename
    # 2026-11-24 19:34 UTC = 14:34 in New York
    assert report_filename(1795548840, "America/New_York") == "SHLPanelStatus_20261124_1434.pdf"
    assert report_filename(1795548840) == "SHLPanelStatus_20261124_1934.pdf"


def test_changes_report():
    from app.report import build_changes_report, report_filename
    rows = [{"ts": 1_800_000_000, "ip": "10.0.0.12", "panel_name": "P12", "slot": 3, "circuit": "1203", "old": "on", "new": "tripped"},
            {"ts": 1_800_000_100, "ip": "10.0.0.99", "panel_name": "Old", "slot": 0, "circuit": None, "old": "online", "new": "offline"}]
    assert build_changes_report(rows, [("10.0.0.12", "Panel 12")], "America/New_York", 1_800_001_000).startswith(b"%PDF")
    assert build_changes_report([], [], None).startswith(b"%PDF")
    assert report_filename(1795548840, "America/New_York", "SHLPanelChanges") == "SHLPanelChanges_20261124_1434.pdf"


def test_net_changes_drops_round_trips():
    from app.report import net_changes
    r = lambda ts, slot, old, new: {"ts": ts, "ip": "a", "panel_name": "P", "slot": slot, "circuit": f"12{slot:02d}", "old": old, "new": new}  # noqa: E731
    rows = [r(1, 1, "off", "on"), r(2, 1, "on", "off"),              # off -> on -> off: same as before, dropped
            r(3, 2, "off", "on"), r(4, 2, "on", "tripped"),          # off -> tripped
            r(5, 3, "on", "off")]
    out = {x["slot"]: (x["was"], x["now"], x["ts"]) for x in net_changes(rows)}
    assert out == {2: ("off", "tripped", 4), 3: ("on", "off", 5)}


def test_changelog_report():
    from app.report import build_changelog_report
    rows = [{"ts": 1_800_000_000, "ip": "a", "panel_name": "P", "slot": 1, "circuit": "1201", "old": "off", "new": "on"},
            {"ts": 1_800_000_050, "ip": "a", "panel_name": "P", "slot": 1, "circuit": "1201", "old": "on", "new": "off"}]
    assert build_changelog_report(rows, [("a", "P")], "America/New_York", 1_800_001_000).startswith(b"%PDF")
    assert build_changelog_report([], [], None).startswith(b"%PDF")


def test_span_label():
    from app.report import span_label
    assert [span_label(h) for h in (1, 6, 24, 48, 168, 720, 36)] == [
        "1 Hour", "6 Hours", "24 Hours", "2 Days", "7 Days", "30 Days", "36 Hours"]
