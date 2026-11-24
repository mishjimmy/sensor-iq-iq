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


def report_filename(now: float, tz_name: str | None = None) -> str:
    """e.g. SHLPanelStatus_20261124_1434.pdf, in the viewer's time zone (UTC if unknown)."""
    d = datetime.fromtimestamp(now, _tz(tz_name) or timezone.utc)
    return d.strftime("SHLPanelStatus_%Y%m%d_%H%M.pdf")


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
