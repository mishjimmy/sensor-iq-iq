"use strict";
// Usage stats page: hand-built SVG charts over /api/history (no external libraries, works offline).

const $ = (s) => document.querySelector(s);
const SVGNS = "http://www.w3.org/2000/svg";
let data = null;
let range = "24h";
let metric = "avg_a";
let energyView = "chart";
let estView = "peak_w";
const OVER = 1.2;  // same threshold as the dashboard: 20%+ over the schedule estimate

// ---- helpers ---------------------------------------------------------------
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v; else if (k === "text") n.textContent = v;
    else if (k === "style") n.style.cssText = v; else n.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}
function s(tag, attrs = {}, text) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  if (text != null) n.textContent = text;
  return n;
}
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmtKW = (w) => w == null ? "—" : (w / 1000 >= 10 ? (w / 1000).toFixed(1) : (w / 1000).toFixed(2)) + " kW";
const fmtKWh = (k) => k == null ? "—" : (k >= 100 ? k.toFixed(0) : k >= 10 ? k.toFixed(1) : k.toFixed(2)) + " kWh";
const fmtA = (a) => (a >= 100 ? a.toFixed(0) : a.toFixed(1)) + " A";
const fmtNum = (v) => Math.abs(v) >= 10000 ? (v / 1000).toFixed(0) + "K" : Number(v.toFixed(2)).toLocaleString();
const tTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const tDay = (ts) => new Date(ts * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
const tFull = (ts) => new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const localDayKey = (ts) => { const d = new Date(ts * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

function niceTicks(max, count = 4) {
  if (!(max > 0)) return { max: 1, ticks: [0, 1] };
  const raw = max / count, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw);
  const top = Math.ceil(max / step) * step;
  const ticks = []; for (let v = 0; v <= top + step / 2; v += step) ticks.push(+v.toFixed(6));
  return { max: top, ticks };
}

// stable colour per panel: index in the registry order (colour follows the entity, never its rank)
function panelColors() {
  const order = [...(data.panel_order || [])];
  for (const ip of Object.keys(data.panels)) if (!order.includes(ip)) order.push(ip);
  const map = {};
  order.forEach((ip, i) => { map[ip] = i < 7 || order.length <= 8 ? `var(--s${i + 1})` : "var(--s-other)"; });
  return { order, map, folded: order.length > 8 };
}
const panelName = (ip) => (data.panels[ip] && data.panels[ip].name) || ip;

// tooltip shared per chart container
function tooltip(container) {
  let tip = container.querySelector(":scope > .tooltip");
  if (!tip) { tip = el("div", { class: "tooltip", hidden: true }); container.append(tip); }
  return {
    show(x, y, nodes) {
      tip.replaceChildren(...nodes); tip.hidden = false;
      const cw = container.clientWidth, tw = tip.offsetWidth, th = tip.offsetHeight;
      let left = x + 14; if (left + tw > cw) left = x - tw - 14; if (left < 0) left = 0;
      tip.style.left = left + "px"; tip.style.top = Math.max(0, y - th - 10) + "px";
    },
    hide() { tip.hidden = true; },
  };
}
const tipRow = (label, value, color) => el("div", { class: "r" },
  el("span", {}, color ? el("i", { class: "sw", style: `background:${color}` }) : null, label), el("b", { text: value }));

function empty(container, msg) { container.replaceChildren(el("div", { class: "chart-empty", text: msg })); }

// ---- KPIs ------------------------------------------------------------------
function renderKpis() {
  const k = data.kpis;
  const tiles = [
    ["Energy used", fmtKWh(k.energy_kwh), `last ${range}`, true],
    ["Average power", fmtKW(k.avg_w), "site total"],
    ["Peak power", fmtKW(k.peak_w), k.peak_ts ? "at " + tFull(k.peak_ts) : ""],
    ["Breaker trips", String(k.trips), "new trips in range"],
  ];
  const ec = estCircuits();
  if (ec.withEst.length) {
    const over = ec.withEst.filter((c) => c.peak_w > c.est * OVER).length;
    tiles.push(["Over estimate", String(over), `of ${ec.withEst.length} circuits, peak 20%+ above schedule`]);
  }
  $("#kpis").replaceChildren(...tiles.map(([l, v, sub, hero]) => el("div", { class: "stat" + ((l === "Breaker trips" || l === "Over estimate") && v !== "0" ? " trip" : "") },
    el("div", { class: "label", text: l }), el("div", { class: "value" + (hero ? " hero" : ""), text: v }), el("div", { class: "sub", text: sub }))));
  $("#coverage").textContent = data.collecting_since ? `History since ${tFull(data.collecting_since)}` : "No history recorded yet";
}

// ---- site power line -------------------------------------------------------
function renderPower() {
  const box = $("#power");
  const pts = data.series;
  if (!pts.length) return empty(box, "No data in this range yet. History is recorded every minute while the service runs.");
  const W = box.clientWidth, H = 240, m = { t: 10, r: 12, b: 24, l: 48 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const x0 = data.start, x1 = data.end;
  const X = (t) => m.l + ((t - x0) / (x1 - x0)) * iw;
  const { max, ticks } = niceTicks(Math.max(...pts.map((p) => p[1])) / 1000);
  const Y = (kw) => m.t + ih - (kw / max) * ih;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": "Site power over time" });

  for (const v of ticks) {
    svg.append(s("line", { class: v ? "gridline" : "baseline", x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
    svg.append(s("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" }, fmtNum(v) + (v === ticks[ticks.length - 1] ? " kW" : "")));
  }
  const span = x1 - x0;
  const tt = timeTicks(x0, x1, Math.max(2, Math.floor(iw / 80)));
  for (const t of tt.ticks) {
    const x = X(t);
    if (x < m.l + 20 || x > W - m.r - 20) continue;
    const midnight = new Date(t * 1000).getHours() === 0;
    svg.append(s("text", { x, y: H - 6, "text-anchor": "middle" }, tt.daily || (midnight && span > 86400) ? tDay(t) : tTime(t)));
  }
  // break the line across gaps (service down / panel offline)
  const gap = data.bucket_s * 2.5;
  let line = "", area = "", segStart = null, prev = null;
  const closeArea = () => { if (segStart != null) area += `L${X(prev[0]).toFixed(1)},${Y(0)}L${X(segStart).toFixed(1)},${Y(0)}Z`; };
  for (const p of pts) {
    const xy = `${X(p[0]).toFixed(1)},${Y(p[1] / 1000).toFixed(1)}`;
    if (!prev || p[0] - prev[0] > gap) { closeArea(); line += `M${xy}`; area += `M${X(p[0]).toFixed(1)},${Y(0)}L${xy}`; segStart = p[0]; }
    else { line += `L${xy}`; area += `L${xy}`; }
    prev = p;
  }
  closeArea();
  svg.append(s("path", { d: area, fill: "var(--s1)", "fill-opacity": 0.1 }));
  svg.append(s("path", { d: line, fill: "none", stroke: "var(--s1)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  // end label on the latest value
  const last = pts[pts.length - 1];
  svg.append(s("circle", { cx: X(last[0]), cy: Y(last[1] / 1000), r: 4, fill: "var(--s1)", stroke: "var(--surface)", "stroke-width": 2 }));

  const cross = s("line", { class: "baseline", y1: m.t, y2: m.t + ih, visibility: "hidden" });
  const dot = s("circle", { r: 5, fill: "var(--s1)", stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" });
  svg.append(cross, dot);
  const hit = s("rect", { class: "hit", x: m.l, y: m.t, width: iw, height: ih });
  svg.append(hit);
  box.replaceChildren(svg);
  const tip = tooltip(box);
  const move = (ev) => {
    const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (W / r.width);
    const t = x0 + ((px - m.l) / iw) * span;
    let best = pts[0];
    for (const p of pts) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
    const cx = X(best[0]), cy = Y(best[1] / 1000);
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", cx); dot.setAttribute("cy", cy); dot.setAttribute("visibility", "visible");
    tip.show(cx * (r.width / W), cy * (r.height / H), [
      el("div", { class: "t", text: tFull(best[0]) + (data.bucket_s > 60 ? ` (${data.bucket_s / 60}-min avg)` : "") }),
      tipRow("Average", fmtKW(best[1]), "var(--s1)"), tipRow("Peak", fmtKW(best[2])), tipRow("Current", fmtA(best[3]))]);
  };
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerleave", () => { tip.hide(); cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });
  $("#power-sub").textContent = `Total across all panels · ${data.bucket_s / 60}-minute averages · latest ${fmtKW(last[1])}`;
}

// ---- energy by panel (stacked columns) ------------------------------------
function energyBuckets() {
  const hourly = range === "6h" || range === "24h";
  const buckets = new Map();
  // pre-create every period in range so empty ones still show
  const step = hourly ? 3600 : 86400;
  for (let t = Math.floor(data.start / 3600) * 3600; t <= data.end; t += step) {
    const key = hourly ? t : localDayKey(t);
    if (!buckets.has(key)) buckets.set(key, { key, ts: t, byPanel: {} });
  }
  for (const [h, ip, avgW, mins] of data.hourly) {
    const key = hourly ? h : localDayKey(h);
    if (!buckets.has(key)) buckets.set(key, { key, ts: h, byPanel: {} });
    const b = buckets.get(key);
    b.byPanel[ip] = (b.byPanel[ip] || 0) + (avgW * mins) / 60 / 1000;  // kWh
  }
  return { hourly, list: [...buckets.values()].sort((a, b) => a.ts - b.ts) };
}

function renderEnergy() {
  const box = $("#energy"), legend = $("#energy-legend");
  const { hourly, list } = energyBuckets();
  $("#energy-title").textContent = hourly ? "Energy by panel, per hour" : "Energy by panel, per day";
  const { order, map, folded } = panelColors();
  const seriesIps = order.filter((ip) => list.some((b) => b.byPanel[ip]));
  // fold panels past slot 7 into "Other" when there are more than 8
  const keyOf = (ip) => folded && order.indexOf(ip) >= 7 ? "__other" : ip;
  const keys = [...new Set(seriesIps.map(keyOf))];
  const colorOf = (k) => k === "__other" ? "var(--s-other)" : map[k];
  const nameOf = (k) => k === "__other" ? "Other panels" : panelName(k);
  const rows = list.map((b) => {
    const v = {}; for (const [ip, kwh] of Object.entries(b.byPanel)) v[keyOf(ip)] = (v[keyOf(ip)] || 0) + kwh;
    return { ...b, v, total: Object.values(v).reduce((a, c) => a + c, 0) };
  });
  legend.replaceChildren(...(keys.length > 1 ? keys.map((k) => el("span", { class: "key" }, el("i", { class: "sw", style: `background:${colorOf(k)}` }), nameOf(k))) : []));
  if (!keys.length) { legend.replaceChildren(); return empty(box, "No energy recorded in this range yet."); }
  const label = (r) => hourly ? tTime(r.ts) : new Date(r.ts * 1000).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });

  if (energyView === "table") {
    const table = el("table", { class: "compact" },
      el("thead", {}, el("tr", {}, el("th", { text: hourly ? "Hour" : "Day" }), keys.map((k) => el("th", { class: "num", text: nameOf(k) })), el("th", { class: "num", text: "Total" }))),
      el("tbody", {}, rows.filter((r) => r.total > 0).map((r) => el("tr", {}, el("td", { text: label(r) }),
        keys.map((k) => el("td", { class: "num", text: r.v[k] ? r.v[k].toFixed(2) : "—" })), el("td", { class: "num", text: r.total.toFixed(2) })))));
    return box.replaceChildren(el("div", { class: "data-table" }, table));
  }

  const W = box.clientWidth, H = 220, m = { t: 10, r: 8, b: 24, l: 48 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const { max, ticks } = niceTicks(Math.max(...rows.map((r) => r.total)));
  const Y = (v) => m.t + ih - (v / max) * ih;
  const band = iw / rows.length, bw = Math.max(3, Math.min(24, band * 0.7));
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": "Energy by panel" });
  for (const v of ticks) {
    svg.append(s("line", { class: v ? "gridline" : "baseline", x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
    svg.append(s("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" }, fmtNum(v) + (v === ticks[ticks.length - 1] ? " kWh" : "")));
  }
  const every = Math.ceil(rows.length / Math.max(1, Math.floor(iw / 64)));
  const tip = tooltip(box);
  rows.forEach((r, i) => {
    const cx = m.l + band * i + band / 2;
    if (i % every === 0) svg.append(s("text", { x: cx, y: H - 6, "text-anchor": "middle" }, hourly ? tTime(r.ts) : tDay(r.ts)));
    let y = Y(0);
    const present = keys.filter((k) => r.v[k] > 0);
    present.forEach((k, j) => {
      const h = Y(0) - Y(r.v[k]);
      const top = j === present.length - 1;
      const gapPx = j > 0 ? 2 : 0;                          // 2px surface gap between stacked segments
      const hh = Math.max(0, h - gapPx);
      y -= h;
      const yy = y;
      if (hh <= 0) return;
      const rr = top ? Math.min(4, hh, bw / 2) : 0;          // 4px rounded data-end, square at the baseline
      const x = cx - bw / 2;
      const d = `M${x},${yy + hh}V${yy + rr}${rr ? `Q${x},${yy} ${x + rr},${yy}` : ""}H${x + bw - rr}${rr ? `Q${x + bw},${yy} ${x + bw},${yy + rr}` : ""}V${yy + hh}Z`;
      svg.append(s("path", { d, fill: colorOf(k) }));
    });
    const hit = s("rect", { class: "row-hit", x: m.l + band * i, y: m.t, width: band, height: ih });
    hit.addEventListener("pointermove", () => {
      const rect = svg.getBoundingClientRect();
      tip.show(cx * (rect.width / W), Y(r.total) * (rect.height / H), [el("div", { class: "t", text: label(r) }),
        ...[...present].reverse().map((k) => tipRow(nameOf(k), fmtKWh(r.v[k]), colorOf(k))),
        present.length > 1 ? tipRow("Total", fmtKWh(r.total)) : null]);
    });
    hit.addEventListener("pointerleave", () => tip.hide());
    svg.insertBefore(hit, svg.firstChild);
  });
  box.replaceChildren(svg, box.querySelector(".tooltip") || "");
}

// ---- horizontal bars (top circuits, rooms) ---------------------------------
function hbars(box, items, { fmt, color = "var(--s1)", tipFor, emptyMsg }) {
  if (!items.length) return empty(box, emptyMsg);
  const W = box.clientWidth, rowH = 26, labelW = Math.min(190, W * 0.38), valueW = 64;
  const H = items.length * rowH + 4, iw = W - labelW - valueW;
  const max = Math.max(...items.map((i) => i.value)) || 1;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img" });
  const tip = tooltip(box);
  items.forEach((it, i) => {
    const y = i * rowH + 2, bh = 14, by = y + (rowH - bh) / 2 - 1;
    const hit = s("rect", { class: "row-hit", x: 0, y, width: W, height: rowH, rx: 4 });
    svg.append(hit);
    const lbl = s("text", { class: "label", x: labelW - 10, y: y + rowH / 2 + 3, "text-anchor": "end" }, it.label);
    svg.append(lbl);
    const w = Math.max(2, (it.value / max) * iw), x = labelW, r = Math.min(4, w / 2);
    svg.append(s("path", { d: `M${x},${by}H${x + w - r}Q${x + w},${by} ${x + w},${by + r}V${by + bh - r}Q${x + w},${by + bh} ${x + w - r},${by + bh}H${x}Z`, fill: it.color || color, "pointer-events": "none" }));
    svg.append(s("text", { class: "value", x: x + w + 6, y: y + rowH / 2 + 3 }, fmt(it.value)));
    hit.addEventListener("pointermove", () => {
      const rect = svg.getBoundingClientRect();
      tip.show((x + w) * (rect.width / W), y * (rect.height / H), tipFor(it));
    });
    hit.addEventListener("pointerleave", () => tip.hide());
  });
  box.replaceChildren(svg, box.querySelector(".tooltip") || "");
  // truncate long labels to the label column
  for (const t of svg.querySelectorAll("text.label")) {
    let txt = t.textContent;
    while (t.getComputedTextLength() > labelW - 14 && txt.length > 4) { txt = txt.slice(0, -2); t.textContent = txt + "…"; }
  }
}

const circuitLabel = (c) => c.circuit || `${panelName(c.ip)} S${c.slot}`;
const circuitRooms = (c) => (c.circuit && data.circuit_info[c.circuit] && data.circuit_info[c.circuit].rooms) || [];

function renderTop() {
  const names = { avg_a: "Highest average current", max_a: "Highest peak current", kwh: "Most energy used", on_hours: "Longest time on" };
  const fmts = { avg_a: fmtA, max_a: fmtA, kwh: fmtKWh, on_hours: (h) => h.toFixed(1) + " h" };
  $("#top-sub").textContent = names[metric] + " in range";
  const items = data.circuits.filter((c) => c[metric] > 0).sort((a, b) => b[metric] - a[metric]).slice(0, 15).map((c) => {
    const rooms = circuitRooms(c);
    return { label: circuitLabel(c) + (rooms.length ? " · " + rooms.join(", ") : ""), value: c[metric], c };
  });
  hbars($("#top"), items, {
    fmt: fmts[metric], emptyMsg: "No circuit history in this range yet.",
    tipFor: ({ c }) => {
      const i = c.circuit && data.circuit_info[c.circuit];
      return [el("div", { class: "t", text: `${circuitLabel(c)} · ${panelName(c.ip)} slot ${c.slot}` }),
        tipRow("Average", fmtA(c.avg_a)), tipRow("Peak", fmtA(c.max_a)), tipRow("Energy", fmtKWh(c.kwh)),
        tipRow("Time on", c.on_hours.toFixed(1) + " h"),
        i && i.rooms.length ? tipRow("Rooms", i.rooms.join(", ")) : null,
        i && i.load_types ? tipRow("Loads", i.load_types) : null];
    },
  });
}

function renderRooms() {
  const rooms = new Map();
  let mapped = false;
  for (const c of data.circuits) {
    const rs = circuitRooms(c);
    if (rs.length) mapped = true;
    const names = rs.length ? rs : ["(not in Grist schedule)"];
    for (const n of names) {
      const r = rooms.get(n) || { label: n, value: 0, circuits: 0, shared: 0 };
      r.value += c.kwh / names.length; r.circuits++; if (names.length > 1) r.shared++;
      rooms.set(n, r);
    }
  }
  if (!mapped && data.circuits.length) return empty($("#rooms"), "Rooms come from the Grist panel schedule. Set GRIST_API_KEY / GRIST_SERVER and each panel's Grist panel number.");
  const items = [...rooms.values()].filter((r) => r.value > 0).sort((a, b) => b.value - a.value).slice(0, 15);
  hbars($("#rooms"), items, {
    fmt: fmtKWh, emptyMsg: "No energy recorded in this range yet.",
    tipFor: (r) => [el("div", { class: "t", text: r.label }), tipRow("Energy", fmtKWh(r.value)),
      tipRow("Circuits", String(r.circuits)), r.shared ? tipRow("Shared with other rooms", String(r.shared)) : null],
  });
}

// ---- actual vs estimate ----------------------------------------------------
function estCircuits() {
  const withEst = [], noEst = [];
  for (const c of data.circuits) {
    const i = c.circuit && data.circuit_info[c.circuit];
    if (i && i.est_w > 0) withEst.push({ ...c, est: i.est_w, info: i, pct: c.peak_w / i.est_w });
    else if (c.peak_w > 5) noEst.push({ ...c, est: null, info: i || null, pct: null });
  }
  return { withEst, noEst };
}

function renderEst() {
  const box = $("#est"), legend = $("#est-legend");
  if (!Object.values(data.circuit_info).some((i) => i.est_w != null)) {
    legend.replaceChildren();
    return empty(box, "Estimates come from fixture wattage in the Grist fixture schedule. Set GRIST_API_KEY / GRIST_SERVER and each panel's Grist panel number.");
  }
  const { withEst, noEst } = estCircuits();
  const over = withEst.filter((c) => c.peak_w > c.est * OVER);
  $("#est-sub").textContent = `${withEst.length} circuits with an estimate · ${over.length} peaked 20%+ over` +
    (noEst.length ? ` · ${noEst.length} drew power with no estimate` : "");
  const pctTxt = (v) => (v == null ? "—" : Math.round(v * 100) + "%");
  const tipFor = (c) => [el("div", { class: "t", text: `${circuitLabel(c)} · ${panelName(c.ip)} slot ${c.slot}` }),
    c.info && c.info.rooms.length ? tipRow("Rooms", c.info.rooms.join(", ")) : null,
    tipRow("Estimate", c.est == null ? "—" : Math.round(c.est) + " W"),
    tipRow("Peak", Math.round(c.peak_w) + " W"), tipRow("Avg while on", c.on_w == null ? "—" : Math.round(c.on_w) + " W"),
    tipRow("Peak vs estimate", pctTxt(c.pct)),
    c.info && c.info.est_unknown ? tipRow("Devices with no wattage", String(c.info.est_unknown)) : null];

  if (estView === "table") {
    legend.replaceChildren();
    const rows = [...withEst].sort((a, b) => b.pct - a.pct).concat([...noEst].sort((a, b) => b.peak_w - a.peak_w));
    const w = (v) => (v == null ? "—" : Math.round(v).toLocaleString());
    const table = el("table", { class: "compact" },
      el("thead", {}, el("tr", {}, ["Circuit", "Panel", "Rooms"].map((h) => el("th", { text: h })),
        ["Est W", "Avg on W", "Peak W", "Peak ÷ est"].map((h) => el("th", { class: "num", text: h })))),
      el("tbody", {}, rows.map((c) => el("tr", {}, el("td", { text: circuitLabel(c) }), el("td", { text: panelName(c.ip) }),
        el("td", { text: c.info ? c.info.rooms.join(", ") : "" }), el("td", { class: "num", text: w(c.est) }),
        el("td", { class: "num", text: w(c.on_w) }), el("td", { class: "num", text: w(c.peak_w) }),
        el("td", { class: "num" + (c.pct > OVER ? " trip-num" : c.pct == null ? " muted" : ""), text: c.pct == null ? "no estimate" : pctTxt(c.pct) })))));
    return box.replaceChildren(el("div", { class: "data-table" }, table));
  }

  // scatter: estimate (x) against measured (y); points above the diagonal drew more than scheduled
  const pts = withEst.filter((c) => c[estView] != null);
  legend.replaceChildren(
    el("span", { class: "key" }, el("i", { class: "sw", style: "background:var(--s1)" }), "Within estimate"),
    el("span", { class: "key" }, el("i", { class: "sw", style: "background:var(--trip)" }), "20%+ over"),
    el("span", { class: "key" }, el("i", { class: "sw", style: "background:var(--axis);height:2px" }), "Actual = estimate"));
  if (!pts.length) return empty(box, "No circuit history in this range yet.");
  const W = box.clientWidth, H = 300, m = { t: 10, r: 14, b: 34, l: 52 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const { max, ticks } = niceTicks(Math.max(...pts.map((c) => Math.max(c.est, c[estView]))));
  const X = (v) => m.l + (v / max) * iw, Y = (v) => m.t + ih - (v / max) * ih;
  const label = estView === "peak_w" ? "Peak" : "Avg while on";
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": `${label} watts against estimated watts per circuit` });
  for (const v of ticks) {
    svg.append(s("line", { class: v ? "gridline" : "baseline", x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
    svg.append(s("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" }, fmtNum(v)));
    if (v) svg.append(s("text", { x: X(v), y: m.t + ih + 16, "text-anchor": "middle" }, fmtNum(v)));
  }
  svg.append(s("text", { x: m.l + iw, y: H - 2, "text-anchor": "end" }, "Estimated W →"));
  svg.append(s("text", { x: m.l + 4, y: m.t + 10 }, label + " W ↑"));
  svg.append(s("line", { x1: X(0), y1: Y(0), x2: X(max), y2: Y(max), stroke: "var(--axis)", "stroke-width": 1.5, "stroke-dasharray": "4 4" }));
  const isOver = (c) => c[estView] > c.est * OVER;
  // draw over-estimate points last so they sit on top
  for (const c of [...pts].sort((a, b) => isOver(a) - isOver(b))) {
    svg.append(s("circle", { cx: X(c.est), cy: Y(c[estView]), r: 4.5, fill: isOver(c) ? "var(--trip)" : "var(--s1)",
      "fill-opacity": 0.75, stroke: "var(--surface)", "stroke-width": 1 }));
  }
  const ring = s("circle", { r: 7, fill: "none", stroke: "var(--text)", "stroke-width": 1.5, visibility: "hidden" });
  const hit = s("rect", { class: "hit", x: m.l, y: m.t, width: iw, height: ih });
  svg.append(ring, hit);
  box.replaceChildren(svg, box.querySelector(".tooltip") || "");
  const tip = tooltip(box);
  hit.addEventListener("pointermove", (ev) => {
    const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (W / r.width), py = (ev.clientY - r.top) * (H / r.height);
    let best = null, bd = 18 * 18;
    for (const c of pts) {
      const d = (X(c.est) - px) ** 2 + (Y(c[estView]) - py) ** 2;
      if (d < bd) { bd = d; best = c; }
    }
    if (!best) { tip.hide(); ring.setAttribute("visibility", "hidden"); return; }
    const cx = X(best.est), cy = Y(best[estView]);
    ring.setAttribute("cx", cx); ring.setAttribute("cy", cy); ring.setAttribute("visibility", "visible");
    tip.show(cx * (r.width / W), cy * (r.height / H), tipFor(best));
  });
  hit.addEventListener("pointerleave", () => { tip.hide(); ring.setAttribute("visibility", "hidden"); });
}

// ---- heatmap ---------------------------------------------------------------
function renderHeat() {
  const box = $("#heat");
  if (!data.hourly.length) return empty(box, "No data in this range yet.");
  const cells = new Map(); // dayKey -> hour -> W
  for (const [h, , avgW] of data.hourly) {
    const d = new Date(h * 1000), key = localDayKey(h);
    if (!cells.has(key)) cells.set(key, { ts: h, hours: {} });
    const row = cells.get(key);
    row.hours[d.getHours()] = (row.hours[d.getHours()] || 0) + avgW;
  }
  const days = [...cells.values()].sort((a, b) => a.ts - b.ts);
  const vals = days.flatMap((d) => Object.values(d.hours));
  const max = Math.max(...vals) || 1;
  const W = box.clientWidth, labelW = 54, cellGap = 2, top = 4;
  const cw = (W - labelW) / 24, ch = Math.max(10, Math.min(22, cw * 0.8));
  const H = top + days.length * ch + 20;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": "Average site power by hour and day" });
  const tip = tooltip(box);
  const color = (v) => `color-mix(in oklab, var(--seq-hi) ${Math.round(12 + 88 * (v / max))}%, var(--surface))`;
  days.forEach((d, row) => {
    const y = top + row * ch;
    svg.append(s("text", { x: labelW - 8, y: y + ch / 2 + 4, "text-anchor": "end" }, tDay(d.ts)));
    for (let hr = 0; hr < 24; hr++) {
      const v = d.hours[hr];
      const x = labelW + hr * cw;
      const rect = s("rect", { x: x + cellGap / 2, y: y + cellGap / 2, width: cw - cellGap, height: ch - cellGap, rx: 2,
        fill: v == null ? "transparent" : color(v), stroke: v == null ? "var(--grid)" : "none" });
      if (v != null) {
        rect.addEventListener("pointermove", () => {
          const r = svg.getBoundingClientRect();
          tip.show((x + cw / 2) * (r.width / W), y * (r.height / H), [
            el("div", { class: "t", text: `${new Date(d.ts * 1000).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}, ${String(hr).padStart(2, "0")}:00` }),
            tipRow("Average", fmtKW(v))]);
        });
        rect.addEventListener("pointerleave", () => tip.hide());
      }
      svg.append(rect);
    }
  });
  for (const hr of [0, 6, 12, 18, 23]) svg.append(s("text", { x: labelW + hr * cw + cw / 2, y: H - 4, "text-anchor": "middle" }, String(hr).padStart(2, "0") + ":00"));
  box.replaceChildren(svg, box.querySelector(".tooltip") || "",
    el("div", { class: "scale" }, "0", el("span", { class: "bar", style: `background:linear-gradient(90deg, ${color(0)}, ${color(max)})` }), fmtKW(max)));
}

// ---- events ----------------------------------------------------------------
function renderEvents() {
  const box = $("#events");
  const evs = data.events;
  const trips = evs.filter((e) => e.new === "tripped");
  $("#events-sub").textContent = `${trips.length} trip(s), ${evs.filter((e) => e.slot === 0).length} panel connectivity change(s) in range`;
  if (!evs.length) return box.replaceChildren(el("div", { class: "chart-empty", text: "No trips or outages in this range." }));
  const counts = new Map();
  for (const e of trips) {
    const k = `${e.ip}|${e.slot}`;
    const r = counts.get(k) || { e, n: 0, last: 0 }; r.n++; r.last = Math.max(r.last, e.ts); counts.set(k, r);
  }
  const most = [...counts.values()].sort((a, b) => b.n - a.n || b.last - a.last).slice(0, 8);
  const label = (e) => e.slot === 0 ? "Panel" : (e.circuit || `S${e.slot}`);
  const rooms = (e) => (e.circuit && data.circuit_info[e.circuit] && data.circuit_info[e.circuit].rooms.join(", ")) || "";
  const change = (e) => e.slot === 0 ? (e.new === "offline" ? "went offline" : "back online") : `${e.old} → ${e.new}`;
  box.replaceChildren(...[
    most.length ? el("div", {}, el("h3", { class: "section", style: "margin-top:4px", text: "Most tripped" }),
      el("table", { class: "compact" }, el("thead", {}, el("tr", {}, el("th", { text: "Circuit" }), el("th", { text: "Panel" }), el("th", { text: "Rooms" }), el("th", { class: "num", text: "Trips" }), el("th", { text: "Last" }))),
        el("tbody", {}, most.map(({ e, n, last }) => el("tr", {}, el("td", { text: label(e) }), el("td", { text: e.panel_name }),
          el("td", { text: rooms(e) }), el("td", { class: "num", text: n }), el("td", { text: tFull(last) })))))) : null,
    el("h3", { class: "section", text: "Recent changes" }),
    el("div", { class: "data-table" }, el("table", { class: "compact" },
      el("thead", {}, el("tr", {}, el("th", { text: "Time" }), el("th", { text: "Panel" }), el("th", { text: "Circuit" }), el("th", { text: "Change" }))),
      el("tbody", {}, evs.slice(0, 100).map((e) => el("tr", {}, el("td", { text: tFull(e.ts) }), el("td", { text: e.panel_name }),
        el("td", { text: label(e) + (rooms(e) ? " · " + rooms(e) : "") }),
        el("td", {}, el("span", { class: "chip " + (e.new === "online" ? "on" : e.new), text: change(e) }))))))),
  ].filter(Boolean));
}

// time ticks on round local boundaries: hours for short ranges, midnights for long ones
function timeTicks(x0, x1, maxTicks) {
  const span = x1 - x0, H = 3600, D = 86400;
  const steps = [H, 2 * H, 3 * H, 6 * H, 12 * H, D, 2 * D, 7 * D];
  const step = steps.find((st) => span / st <= maxTicks) || 7 * D;
  const d = new Date(x0 * 1000);
  if (step < D) {
    d.setMinutes(0, 0, 0);
    if (d.getTime() < x0 * 1000) d.setHours(d.getHours() + 1);
    while (d.getHours() % (step / H)) d.setHours(d.getHours() + 1);
  } else {
    d.setHours(24, 0, 0, 0);  // next local midnight
  }
  const out = [];
  for (let t = d.getTime() / 1000; t <= x1; ) {
    out.push(t);
    const n = new Date(t * 1000);
    if (step < D) n.setHours(n.getHours() + step / H); else n.setDate(n.getDate() + step / D);
    t = n.getTime() / 1000;
  }
  return { ticks: out, daily: step >= D };
}

// ---- load / wiring ---------------------------------------------------------
function renderAll() {
  if (!data) return;
  renderKpis(); renderPower(); renderEnergy(); renderTop(); renderEst(); renderRooms(); renderHeat(); renderEvents();
  $("#updated").textContent = "Updated " + new Date().toLocaleTimeString();
}

async function load() {
  try {
    const r = await fetch("/api/history?range=" + encodeURIComponent(range));
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    data = await r.json();
    $("#banners").replaceChildren();
    renderAll();
  } catch (e) {
    $("#banners").replaceChildren(el("div", { class: "banner err", text: "Could not load history: " + e.message }));
  }
}

function press(sel, attr, val) { document.querySelectorAll(sel).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset[attr] === val))); }

try { const saved = localStorage.getItem("siq.range"); if (saved) range = saved; } catch {}
const urlRange = new URLSearchParams(location.search).get("range");
if (["6h", "24h", "7d", "30d"].includes(urlRange)) range = urlRange;
press(".range button", "range", range);
document.querySelectorAll(".range button").forEach((b) => b.addEventListener("click", () => {
  range = b.dataset.range; press(".range button", "range", range);
  try { localStorage.setItem("siq.range", range); } catch {}
  load();
}));
document.querySelectorAll("[data-metric]").forEach((b) => b.addEventListener("click", () => { metric = b.dataset.metric; press("[data-metric]", "metric", metric); renderTop(); }));
document.querySelectorAll("[data-est]").forEach((b) => b.addEventListener("click", () => { estView = b.dataset.est; press("[data-est]", "est", estView); renderEst(); }));
document.querySelectorAll("[data-energy]").forEach((b) => b.addEventListener("click", () => { energyView = b.dataset.energy; press("[data-energy]", "energy", energyView); renderEnergy(); }));
let resizeT;
new ResizeObserver(() => { clearTimeout(resizeT); resizeT = setTimeout(renderAll, 150); }).observe(document.querySelector("main"));
setInterval(load, 60000);
load();
