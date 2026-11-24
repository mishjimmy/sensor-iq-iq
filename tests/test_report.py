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
