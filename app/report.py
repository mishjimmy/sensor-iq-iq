"""Plain PDF status report: panels side by side, each panel's circuits on one page."""
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fpdf import FPDF

PER_PAGE = 4          # panels side by side on each page
MARGIN = 12           # mm
GAP = 7               # mm between panel columns
ROW_MAX = 5.0         # mm; rows shrink below this so a full 48-slot panel still fits one page
FILL = {"on": (214, 240, 220), "tripped": (248, 208, 204)}  # light green / light red


def _tz(name: str | None):
    try:
        return ZoneInfo(name) if name else None
    except Exception:  # unknown zone name or no tz database: fall back to UTC
        return None


def _when(ts: float | None, tz) -> str:
    if not ts:
        return "never"
    d = datetime.fromtimestamp(ts, tz or timezone.utc)
    return d.strftime("%Y-%m-%d %H:%M") + ("" if tz else " UTC")


def report_filename(now: float, tz_name: str | None = None, prefix: str = "SHLPanelStatus") -> str:
    """e.g. SHLPanelStatus_20261124_1434.pdf, in the viewer's time zone (UTC if unknown)."""
    d = datetime.fromtimestamp(now, _tz(tz_name) or timezone.utc)
    return d.strftime(f"{prefix}_%Y%m%d_%H%M.pdf")


def span_label(hours: int) -> str:
    """24 -> '24 Hours', 1 -> '1 Hour', 168 -> '7 Days'."""
    if hours >= 48 and hours % 24 == 0:
        return f"{hours // 24} Days"
    return f"{hours} Hour{'s' if hours != 1 else ''}"


def _txt(s: Any) -> str:
    # built-in PDF fonts are Latin-1 only
    return str(s).encode("latin-1", "replace").decode("latin-1")


def _fit(pdf: FPDF, s: str, w: float) -> str:
    s = _txt(s)
    if pdf.get_string_width(s) <= w:
        return s
    while s and pdf.get_string_width(s + "...") > w:
        s = s[:-1]
    return s + "..."


def _since(c: dict[str, Any], tz) -> str:
    """Short last-change time; '*' marks 'unchanged since monitoring began' (real change is earlier)."""
    ts = c.get("changed_at")
    if not ts:
        return ""
    s = datetime.fromtimestamp(ts, tz or timezone.utc).strftime("%m/%d %H:%M")
    return s if c.get("change_known") else s + "*"


def _panel(pdf: FPDF, p: dict[str, Any], x: float, y: float, w: float, bottom: float, tz) -> bool:
    """Draw one panel column; returns True if any time is marked '*'."""
    panel = p["panel"]
    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(w, 5, _fit(pdf, panel["name"], w), new_x="LEFT", new_y="NEXT")
    pdf.set_font("Helvetica", "", 7.5)
    pdf.cell(w, 4, _fit(pdf, panel["ip"], w), new_x="LEFT", new_y="NEXT")
    if not p["online"]:
        pdf.cell(w, 4, "Unreachable", new_x="LEFT", new_y="NEXT")
        pdf.cell(w, 4, _fit(pdf, f"Last seen {_when(p.get('last_ok'), tz)}", w), new_x="LEFT", new_y="NEXT")
        return False
    counts = p["summary"]["counts"]
    pdf.cell(w, 4, _fit(pdf, f"{counts['on']} on, {counts['off']} off, {counts['tripped']} tripped", w),
             new_x="LEFT", new_y="NEXT")
    pdf.set_xy(x, pdf.get_y() + 1)
    rows = [c for c in p["circuits"] if c["status"] != "empty" and not c.get("pole_of")]
    rh = min(ROW_MAX, (bottom - pdf.get_y()) / (len(rows) + 1))
    cw, sw = w * 0.24, w * 0.31          # circuit, status; the rest is the last-change time
    tw = w - cw - sw
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.cell(cw, rh, "Circuit", border=1)
    pdf.cell(sw, rh, "Status", border=1)
    pdf.cell(tw, rh, "Last change", border=1, new_x="LEFT", new_y="NEXT")
    pdf.set_x(x)
    starred = False
    for c in rows:
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(cw, rh, _txt(c["circuit"] or f"Slot {c['slot']}"), border=1)
        fill = FILL.get(c["status"])
        if fill:
            pdf.set_fill_color(*fill)
        pdf.cell(sw, rh, c["status"].capitalize(), border=1, fill=bool(fill))
        since = _since(c, tz)
        starred |= since.endswith("*")
        pdf.set_font("Helvetica", "", 7)
        pdf.cell(tw, rh, since, border=1, new_x="LEFT", new_y="NEXT")
        pdf.set_x(x)
    return starred


def build_report(snapshot: dict[str, Any], tz_name: str | None = None, now: float | None = None) -> bytes:
    tz = _tz(tz_name)
    now = time.time() if now is None else now
    pdf = FPDF(format="Letter")
    pdf.set_auto_page_break(False)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    panels = [p for p in snapshot["panels"] if p["panel"].get("enabled", True)]
    col_w = (pdf.w - 2 * MARGIN - GAP * (PER_PAGE - 1)) / PER_PAGE
    bottom = pdf.h - MARGIN - 5  # room for the footnote
    for i in range(0, max(1, len(panels)), PER_PAGE):
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 6, "Circuit Status Report")
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, f"Generated {_when(now, tz)}", align="R", new_x="LMARGIN", new_y="NEXT")
        top = pdf.get_y() + 4
        starred = False
        for j, p in enumerate(panels[i:i + PER_PAGE]):
            starred |= _panel(pdf, p, MARGIN + j * (col_w + GAP), top, col_w, bottom, tz)
        if starred:
            pdf.set_xy(MARGIN, pdf.h - MARGIN - 4)
            pdf.set_font("Helvetica", "", 7)
            pdf.cell(0, 4, "* No change seen since monitoring began at this time; the actual change was earlier.")
    return bytes(pdf.output())


# ---- changes report ----------------------------------------------------------------
# Net changes: each circuit's status at the start of the window against its status now.
# A circuit that went off and came back on within the window is not listed.
CHANGE_COLS = (("Circuit", 24), ("Was", 26), ("Now", 26), ("Changed", 34))  # mm


def net_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse a window of status changes (oldest first) to one row per circuit whose status
    now differs from the start of the window: was = first 'old', now = last 'new'."""
    net: dict[tuple[str, int], dict[str, Any]] = {}
    for c in sorted(changes, key=lambda r: r["ts"]):
        key = (c["ip"], c["slot"])
        if key not in net:
            net[key] = {**c, "was": c["old"]}
        net[key].update(ts=c["ts"], new=c["new"], circuit=c["circuit"] or net[key]["circuit"])
    return [{**r, "now": r["new"]} for r in net.values() if r["was"] != r["new"]]


def _change_header(pdf: FPDF) -> None:
    pdf.set_font("Helvetica", "B", 8.5)
    for label, w in CHANGE_COLS:
        pdf.cell(w, 5.5, label, border=1)
    pdf.ln()


def _status_cell(pdf: FPDF, w: float, status: str) -> None:
    fill = FILL.get("on" if status == "online" else status)
    if fill:
        pdf.set_fill_color(*fill)
    pdf.cell(w, 5.5, str(status).capitalize(), border=1, fill=bool(fill))


def build_changes_report(changes: list[dict[str, Any]], panel_order: list[tuple[str, str]],
                         tz_name: str | None = None, now: float | None = None, hours: int = 24) -> bytes:
    """changes: rows from History.changes_since; panel_order: [(ip, name)] in registry order."""
    tz = _tz(tz_name)
    now = time.time() if now is None else now
    pdf = FPDF(format="Letter")
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_auto_page_break(True, margin=MARGIN)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 6, f"Circuit Changes - Last {span_label(hours)}")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Generated {_when(now, tz)}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Circuits whose status now differs from {_when(now - hours * 3600, tz)}",
             new_x="LMARGIN", new_y="NEXT")

    by_ip: dict[str, list[dict[str, Any]]] = {}
    for c in net_changes(changes):
        by_ip.setdefault(c["ip"], []).append(c)
    names = dict(panel_order)
    order = [ip for ip, _ in panel_order if ip in by_ip] + [ip for ip in by_ip if ip not in names]
    if not order:
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"No circuits differ from {span_label(hours).lower()} ago.")
        return bytes(pdf.output())

    for ip in order:
        # the panel's own online/offline row first, then circuits in slot order
        rows = sorted(by_ip[ip], key=lambda r: r["slot"])
        name = names.get(ip) or rows[0]["panel_name"] or ip
        if pdf.get_y() > pdf.h - MARGIN - 30:  # don't strand a heading at the bottom of a page
            pdf.add_page()
        pdf.ln(5)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 6, _txt(name), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        n = sum(1 for r in rows if r["slot"])
        pdf.cell(0, 4.5, _txt(f"{ip}  {n} circuit{'s' if n != 1 else ''} changed"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        _change_header(pdf)
        for r in rows:
            if pdf.get_y() > pdf.h - MARGIN - 6:
                pdf.add_page()
                _change_header(pdf)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.cell(CHANGE_COLS[0][1], 5.5, _txt("Panel" if r["slot"] == 0 else (r["circuit"] or f"Slot {r['slot']}")), border=1)
            _status_cell(pdf, CHANGE_COLS[1][1], r["was"])
            _status_cell(pdf, CHANGE_COLS[2][1], r["now"])
            pdf.cell(CHANGE_COLS[3][1], 5.5, datetime.fromtimestamp(r["ts"], tz or timezone.utc).strftime("%m/%d %H:%M"), border=1)
            pdf.ln()
    return bytes(pdf.output())


# ---- changelog report: every individual change, newest first ---------------------------
LOG_COLS = (("Time", 34), ("Circuit", 24), ("From", 26), ("To", 26))  # mm


def _log_header(pdf: FPDF) -> None:
    pdf.set_font("Helvetica", "B", 8.5)
    for label, w in LOG_COLS:
        pdf.cell(w, 5.5, label, border=1)
    pdf.ln()


def build_changelog_report(changes: list[dict[str, Any]], panel_order: list[tuple[str, str]],
                           tz_name: str | None = None, now: float | None = None, hours: int = 24) -> bytes:
    """Every circuit status change and panel online/offline change in the window, grouped by panel."""
    tz = _tz(tz_name)
    now = time.time() if now is None else now
    pdf = FPDF(format="Letter")
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_auto_page_break(True, margin=MARGIN)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 6, f"Circuit Changelog - Last {span_label(hours)}")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Generated {_when(now, tz)}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Every status change since {_when(now - hours * 3600, tz)}", new_x="LMARGIN", new_y="NEXT")

    by_ip: dict[str, list[dict[str, Any]]] = {}
    for c in changes:
        by_ip.setdefault(c["ip"], []).append(c)
    names = dict(panel_order)
    order = [ip for ip, _ in panel_order if ip in by_ip] + [ip for ip in by_ip if ip not in names]
    if not order:
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"No changes in the last {span_label(hours).lower()}.")
        return bytes(pdf.output())

    for ip in order:
        rows = sorted(by_ip[ip], key=lambda r: r["ts"], reverse=True)  # newest first
        name = names.get(ip) or rows[0]["panel_name"] or ip
        if pdf.get_y() > pdf.h - MARGIN - 30:
            pdf.add_page()
        pdf.ln(5)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 6, _txt(name), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(0, 4.5, _txt(f"{ip}  {len(rows)} change{'s' if len(rows) != 1 else ''}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        _log_header(pdf)
        for r in rows:
            if pdf.get_y() > pdf.h - MARGIN - 6:
                pdf.add_page()
                _log_header(pdf)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.cell(LOG_COLS[0][1], 5.5, datetime.fromtimestamp(r["ts"], tz or timezone.utc).strftime("%m/%d %H:%M:%S"), border=1)
            pdf.cell(LOG_COLS[1][1], 5.5, _txt("Panel" if r["slot"] == 0 else (r["circuit"] or f"Slot {r['slot']}")), border=1)
            pdf.cell(LOG_COLS[2][1], 5.5, str(r["old"]).capitalize(), border=1)
            _status_cell(pdf, LOG_COLS[3][1], r["new"])
            pdf.ln()
    return bytes(pdf.output())
