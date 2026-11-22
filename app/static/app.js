"use strict";
// SensorIQ Monitor dashboard. Read-only: only talks to this service's /api/state + /api/stream.

const $ = (s) => document.querySelector(s);
let state = null;
let view = "panels";

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}

const fmtA = (a) => (a >= 100 ? a.toFixed(0) : a.toFixed(1)) + " A";
const fmtW = (w) => (w >= 1000 ? (w / 1000).toFixed(2) + " kW" : Math.round(w) + " W");
function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return Math.round(s) + "s ago";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  return Math.round(s / 3600) + "h ago";
}

// ---- filtering -------------------------------------------------------------
function filters() {
  try { localStorage.setItem("siq.filters", JSON.stringify({ q: $("#q").value, p: $("#panelFilter").value, t: $("#trippedOnly").checked, e: $("#compareEst").checked, view })); } catch {}
  return { q: $("#q").value.trim().toLowerCase(), panel: $("#panelFilter").value, tripped: $("#trippedOnly").checked, est: $("#compareEst").checked };
}
function info(c) { return (c.circuit && state.circuit_info[c.circuit]) || null; }

// ---- actual vs estimate ----------------------------------------------------
// Estimate = SUM of Wattage for everything on the circuit in Grist's Fixture_Schedule (same as the
// panel schedule page). It's a full-on / rated figure, so dimmed loads read under 100%.
const CIRCUIT_W = 2400, CONTINUOUS_W = 1800;  // per-circuit allocation and 80% continuous-load line
const OVER = 1.2;  // flag circuits drawing 20%+ more than the schedule says (driver/PSU losses ~10%)
const kw = (w) => (w >= 10000 ? (w / 1000).toFixed(0) + "k" : w >= 1000 ? (w / 1000).toFixed(1) + "k" : String(Math.round(w)));
function estOf(c) {
  const i = info(c);
  return i && i.est_w != null ? i.est_w : null;
}
const compareable = (c) => !c.pole_of && (c.status === "on" || c.status === "off");
function estTotals(circuits) {
  // only circuits that are on and have an estimate: an off circuit's estimate isn't "expected" draw
  let actual = 0, est = 0, over = 0, unsched = 0;
  for (const c of circuits) {
    if (c.status !== "on" || c.pole_of) continue;
    const e = estOf(c);
    if (e == null || e <= 0) { if (c.power_w > 5) unsched++; continue; }
    actual += c.power_w; est += e;
    if (c.power_w > e * OVER) over++;
  }
  return { actual, est, over, unsched, pct: est ? Math.round((actual / est) * 100) : null };
}
function estTip(c) {
  const i = info(c);
  if (!i) return [];
  const out = [];
  if (i.est_w != null) {
    out.push(`Estimate: ${Math.round(i.est_w)} W from ${i.est_loads} device${i.est_loads === 1 ? "" : "s"}` +
      (i.est_unknown ? ` (${i.est_unknown} with no wattage)` : ""));
    if (c.status === "on" && i.est_w > 0) out.push(`Actual: ${Math.round(c.power_w)} W = ${Math.round((c.power_w / i.est_w) * 100)}% of estimate`);
  } else if (i.est_loads) out.push(`Estimate: none of the ${i.est_loads} device(s) on this circuit has a wattage`);
  return out;
}
function matches(c, panel, f) {
  if (f.tripped && c.status !== "tripped") return false;
  if (!f.q) return true;
  const i = info(c);
  const hay = [c.circuit, "slot " + c.slot, panel.name, panel.room, i && i.rooms.join(" "), i && (i.anchors || []).join(" "), i && i.load_types]
    .filter(Boolean).join(" ").toLowerCase();
  return hay.includes(f.q);
}

// dismissed offline-panel alerts, per browser (a convenience; safe if storage is unavailable)
function loadDismissed() {
  try { return new Set(JSON.parse(localStorage.getItem("siq.dismissed") || "[]")); } catch { return new Set(); }
}
function saveDismissed(set) {
  try { localStorage.setItem("siq.dismissed", JSON.stringify([...set])); } catch {}
}

// ---- header ----------------------------------------------------------------
function renderStats() {
  const s = state.summary, c = s.counts;
  const online = state.panels.filter((p) => p.online).length;
  const tiles = [
    ["Total current", fmtA(s.total_a), ""],
    ["Total power", fmtW(s.total_w), ""],
    ["Panels online", `${online} / ${state.panels.length}`, online < state.panels.length ? "offline" : ""],
    ["On", c.on, "on"], ["Off", c.off, ""], ["Tripped", c.tripped, c.tripped ? "trip" : ""],
    ["Offline circuits", c.offline, c.offline ? "offline" : ""],
  ];
  if ($("#compareEst").checked) {
    const et = estTotals(state.panels.filter((p) => p.online).flatMap((p) => p.circuits));
    tiles.push(["Actual vs estimate", et.pct == null ? "—" : et.pct + "%", ""],
      ["Over estimate", et.over, et.over ? "trip" : ""]);
    if (et.unsched) tiles.push(["On, no estimate", et.unsched, "offline"]);
  }
  $("#stats").replaceChildren(...tiles.map(([l, v, cls]) =>
    el("div", { class: "stat " + cls }, el("div", { class: "label", text: l }), el("div", { class: "value", text: v }))));
  $("#updated").textContent = "Updated " + ago(state.updated_at);

  const g = state.grist, gp = $("#grist");
  gp.hidden = false;
  if (!g.enabled) gp.replaceChildren(el("span", { class: "dot" }), "Grist sync off");
  else if (g.error) gp.replaceChildren(el("span", { class: "dot bad" }), "Grist: " + g.error.slice(0, 60));
  else gp.replaceChildren(el("span", { class: "dot ok" }), "Grist synced " + ago(g.last_sync));
  if (g.error) gp.title = g.error;

  const banners = [];
  if (state.registry_error) banners.push(el("div", { class: "banner err" }, "panels.yaml has an error (using last good config): ", state.registry_error));

  // unreachable-panel alerts: closeable; a dismissal lasts until that panel is back online
  const dismissed = loadDismissed();
  let changed = false;
  for (const p of state.panels) if (p.online && dismissed.delete(p.panel.ip)) changed = true;
  if (changed) saveDismissed(dismissed);
  const offline = state.panels.filter((p) => p.panel.enabled && p.polled && !p.online);
  const shown = offline.filter((p) => !dismissed.has(p.panel.ip));
  const hidden = offline.length - shown.length;
  for (const p of shown) {
    const close = el("button", { class: "banner-close", type: "button", title: "Dismiss until this panel is back online", "aria-label": `Dismiss alert for ${p.panel.name}`, text: "×" });
    close.addEventListener("click", () => { dismissed.add(p.panel.ip); saveDismissed(dismissed); render(); });
    banners.push(el("div", { class: "banner warn closable" },
      el("span", { text: `${p.panel.name} (${p.panel.ip}) is unreachable: ${p.last_error || ""} — last seen ${ago(p.last_ok)}` }), close));
  }
  if (shown.length > 1 || hidden) {
    const bar = el("div", { class: "banner-bar muted" });
    if (shown.length > 1) {
      const all = el("button", { class: "linkish", type: "button", text: `Hide all ${shown.length}` });
      all.addEventListener("click", () => { shown.forEach((p) => dismissed.add(p.panel.ip)); saveDismissed(dismissed); render(); });
      bar.append(all);
    }
    if (hidden) {
      const show = el("button", { class: "linkish", type: "button", text: "Show" });
      show.addEventListener("click", () => { saveDismissed(new Set()); render(); });
      bar.append(el("span", { text: `${shown.length > 1 ? " · " : ""}${hidden} unreachable panel alert${hidden > 1 ? "s" : ""} hidden · ` }), show);
    }
    banners.push(bar);
  }
  $("#banners").replaceChildren(...banners);

  const sel = $("#panelFilter"), cur = sel.value;
  const opts = [el("option", { value: "", text: "All panels" }),
    ...state.panels.map((p) => el("option", { value: p.panel.ip, text: p.panel.name }))];
  sel.replaceChildren(...opts);
  sel.value = state.panels.some((p) => p.panel.ip === cur) ? cur : "";
}

// ---- panels view -----------------------------------------------------------
function slotTile(c, p, f, side) {
  const i = info(c);
  const rooms = i ? i.rooms.join(", ") : "";
  const tip = [
    c.circuit ? `Circuit ${c.circuit}` : `Slot ${c.slot}`, `Slot ${c.slot} · ${c.status}`,
    c.status !== "empty" && c.status !== "offline" ? `${c.current_a.toFixed(2)} A · ${Math.round(c.power_w)} W · ${c.voltage} V` : null,
    rooms && `Rooms: ${rooms}`, i && i.anchors && i.anchors.length && `Anchor: ${i.anchors.join(", ")}`, i && i.load_types && `Loads: ${i.load_types}`, i && `Fixtures: ${i.fixture_count}`,
    ...estTip(c),
    c.pole_of && `Pole 2 of slot ${c.pole_of}`, c.breaker_type && c.breaker_type !== "None" && c.breaker_type,
    ...(c.errors || []),
  ].filter(Boolean).join("\n");
  let amps = c.status === "tripped" ? "TRIP" : c.status === "empty" ? "—" : c.status === "offline" ? "?"
    : c.pole_of ? "⇡" : fmtA(c.current_a);
  let cls = "", style = null;
  if (f.est && compareable(c)) {
    // actual / estimate in watts, with a bar showing the ratio (full + red when over)
    const e = estOf(c), on = c.status === "on";
    amps = `${on ? kw(c.power_w) : "–"}/${e != null ? kw(e) : "?"} W`;
    cls = " cmp bar";
    if (on && e > 0) {
      const r = c.power_w / e;
      style = `--pct:${Math.min(100, Math.round(r * 100))}`;
      if (r > OVER) cls += " over";
    } else if (on && c.power_w > 5) cls += " unsched";  // drawing power with nothing scheduled
  } else if (compareable(c) && c.power_w > 0) {
    // circuit capacity: share of the 2400 W allocation (20 A x 120 V); amber past the 1800 W continuous limit
    cls = " bar" + (c.power_w > CIRCUIT_W ? " over" : c.power_w > CONTINUOUS_W ? " warn" : "");
    style = `--pct:${Math.min(100, Math.round((c.power_w / CIRCUIT_W) * 100))}`;
  }
  // like a printed panel schedule: circuit numbers sit next to the centre bus, descriptions on the outside
  const parts = [el("span", { class: "room", text: rooms }), el("span", { class: "amps", text: amps }),
    el("span", { class: "id", text: c.circuit || "S" + c.slot })];
  return el("div", { class: `slot ${side} ${c.status}${cls}${matches(c, p.panel, f) ? "" : " dim"}`, title: tip, style },
    side === "left" ? parts : parts.reverse());
}

// two columns like a relay panel: odd slots down the left, even slots down the right
function panelSchedule(p, f) {
  const bySlot = new Map(p.circuits.map((c) => [c.slot, c]));
  const maxSlot = Math.max(0, ...p.circuits.map((c) => c.slot));
  const rows = [];
  for (let odd = 1; odd <= maxSlot; odd += 2) {
    const l = bySlot.get(odd), r = bySlot.get(odd + 1);
    rows.push(l ? slotTile(l, p, f, "left") : el("div", { class: "slot left none" }),
      r ? slotTile(r, p, f, "right") : el("div", { class: "slot right none" }));
  }
  return el("div", { class: "slots" }, rows);
}

// online panels first (registry order); offline and disabled ones sink to the bottom
function panelRank(p) {
  if (!p.panel.enabled) return 2;
  return p.polled && !p.online ? 1 : 0;
}

function renderPanels(f) {
  const list = state.panels.filter((p) => !f.panel || p.panel.ip === f.panel)
    .map((p, i) => [p, i]).sort((a, b) => panelRank(a[0]) - panelRank(b[0]) || a[1] - b[1]).map(([p]) => p);
  if (!state.panels.length) {
    $("#view-panels").replaceChildren(el("div", { class: "empty-state" }, "No panels configured yet. ",
      el("a", { href: "/panels", text: "Add a panel" }), " or edit panels.yaml."));
    return;
  }
  $("#view-panels").replaceChildren(...list.map((p) => {
    const pn = p.panel, s = p.summary, inf = p.info || {};
    const status = !pn.enabled ? ["", "disabled"] : !p.polled ? ["", "waiting"] : p.online ? ["ok", "online"] : ["bad", "offline"];
    const meta = [pn.room, pn.ip, pn.grist_panel != null ? `Grist panel ${pn.grist_panel}` : "no Grist panel",
      inf.incoming_voltage ? `${inf.incoming_voltage} V in` : null, inf.firmware ? `fw ${inf.firmware}` : null].filter(Boolean).join(" · ");
    const errs = (inf.errors || []);
    const et = f.est && p.online ? estTotals(p.circuits) : null;
    return el("article", { class: "card" + (pn.enabled && p.polled && !p.online ? " offline" : "") },
      el("div", { class: "card-head" },
        el("h2", {}, el("span", { class: "dot " + status[0], title: status[1] }), pn.name),
        el("span", { class: "meta", text: meta }),
        el("span", { class: "totals" }, fmtA(s.total_a), " · ", fmtW(s.total_w),
          s.counts.tripped ? el("span", { class: "trip", text: `${s.counts.tripped} tripped` }) : null,
          et && et.est ? el("span", { class: "est" + (et.over ? " over" : ""), title: "Circuits that are on and have a fixture-schedule estimate",
            text: `on: ${fmtW(et.actual)} of ${fmtW(et.est)} est (${et.pct}%)${et.over ? ` · ${et.over} over` : ""}` }) : null)),
      errs.length && p.online ? el("details", { class: "card-errors" }, el("summary", { text: `${errs.length} panel alert(s)` }),
        el("div", { text: errs.join(" · ") })) : null,
      panelSchedule(p, f));
  }));
}

// ---- rooms view ------------------------------------------------------------
// Controls live in index.html (outside the re-rendered table) so typing isn't interrupted by live updates.
const roomsUI = { group: "room", q: "", trips: false, idle: false, sort: { key: "dedicated", dir: -1 } };
try { Object.assign(roomsUI, JSON.parse(localStorage.getItem("siq.rooms") || "{}")); } catch {}
const saveRoomsUI = () => { try { localStorage.setItem("siq.rooms", JSON.stringify(roomsUI)); } catch {} };

const GROUPS = {
  room: { label: "Served room", none: "(not in Grist schedule)", keys: (c, p, i) => (i && i.rooms.length ? i.rooms : null) },
  anchor: { label: "Anchor", none: "(no anchor)", keys: (c, p, i) => (i && i.anchors && i.anchors.length ? i.anchors : null) },
  location: { label: "Panel location", none: "(no location set)", keys: (c, p) => (p.panel.room ? [p.panel.room] : null) },
};
const anchorName = (code) => (state.anchor_names && state.anchor_names[code]) || "";

function roomGroups(f) {
  const g = GROUPS[roomsUI.group] || GROUPS.room;
  const groups = new Map();
  for (const p of state.panels) {
    if (f.panel && p.panel.ip !== f.panel) continue;
    for (const c of p.circuits) {
      if (c.status === "empty" || c.pole_of || !matches(c, p.panel, f)) continue;
      const names = g.keys(c, p, info(c)) || [g.none];
      const shared = names.length > 1;
      for (const n of names) {
        if (!groups.has(n)) groups.set(n, { name: n, detail: roomsUI.group === "anchor" ? anchorName(n) : "",
          dedicated: 0, shared: 0, count: 0, on: 0, tripped: 0, offline: 0, circuits: [] });
        const r = groups.get(n);
        r[shared ? "shared" : "dedicated"] += c.current_a;
        r.count++; r.circuits.push({ c, shared });
        if (c.status === "on") r.on++;
        if (c.status === "tripped") r.tripped++;
        if (c.status === "offline") r.offline++;
      }
    }
  }
  return [...groups.values()];
}

const ROOM_COLS = [
  { key: "name", label: () => GROUPS[roomsUI.group].label, num: false },
  { key: "detail", label: () => "Name", num: false, only: "anchor" },
  { key: "dedicated", label: () => "Current", num: true, tip: "Current on circuits that serve only this group" },
  { key: "shared", label: () => "+ Shared", num: true, tip: "Current on circuits shared with other groups (not double-counted in Current)" },
  { key: "count", label: () => "Circuits", num: true },
  { key: "on", label: () => "On", num: true },
  { key: "tripped", label: () => "Tripped", num: true },
];

function renderRooms(f) {
  let rows = roomGroups(f);
  const q = roomsUI.q.trim().toLowerCase();
  if (q) rows = rows.filter((r) => (r.name + " " + r.detail).toLowerCase().includes(q));
  if (roomsUI.trips) rows = rows.filter((r) => r.tripped > 0);
  if (roomsUI.idle) rows = rows.filter((r) => r.dedicated + r.shared > 0.05 || r.on > 0);
  const { key, dir } = roomsUI.sort;
  rows.sort((a, b) => {
    const va = a[key], vb = b[key];
    const cmp = typeof va === "string" ? va.localeCompare(vb, undefined, { numeric: true }) : va - vb;
    return cmp * dir || a.name.localeCompare(b.name, undefined, { numeric: true });
  });

  const cols = ROOM_COLS.filter((c) => !c.only || c.only === roomsUI.group);
  const head = cols.map((col) => {
    const active = key === col.key;
    const b = el("button", { class: "sort" + (active ? " active" : ""), type: "button", title: col.tip || `Sort by ${col.label()}` },
      col.label(), el("span", { class: "arrow", text: active ? (dir > 0 ? "▲" : "▼") : "↕" }));
    b.addEventListener("click", () => {
      roomsUI.sort = active ? { key, dir: -dir } : { key: col.key, dir: col.num ? -1 : 1 };
      saveRoomsUI(); render();
    });
    return el("th", { class: col.num ? "num" : "", "aria-sort": active ? (dir > 0 ? "ascending" : "descending") : "none" }, b);
  });
  const groupWord = { room: "rooms", anchor: "anchors", location: "locations" }[roomsUI.group];
  const chip = ({ c, shared }) => el("span", {
    class: `chip ${c.status}${shared ? " shared" : ""}`,
    title: `${c.circuit || "slot " + c.slot}: ${c.status}, ${c.current_a.toFixed(2)} A${shared ? ` (shared with other ${groupWord})` : ""}`,
    text: `${c.circuit || "S" + c.slot} ${c.status === "tripped" ? "TRIP" : c.status === "offline" ? "?" : c.current_a.toFixed(1)}`,
  });
  const cell = (r, col) => {
    if (col.key === "name") return el("td", { text: r.name });
    if (col.key === "detail") return el("td", { class: "muted", text: r.detail });
    if (col.key === "dedicated") return el("td", { class: "num", text: fmtA(r.dedicated) });
    if (col.key === "shared") return el("td", { class: "num muted", text: r.shared ? fmtA(r.shared) : "—" });
    if (col.key === "tripped") return el("td", { class: "num" + (r.tripped ? " trip-num" : ""), text: r.tripped || "—" });
    return el("td", { class: "num", text: r[col.key] });
  };
  const total = rows.reduce((a, r) => a + r.dedicated, 0);
  const table = el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, head, el("th", { text: `Circuits (dashed = shared with other ${groupWord})` }))),
    el("tbody", {}, rows.length ? rows.map((r) => el("tr", {}, cols.map((col) => cell(r, col)),
      el("td", {}, el("div", { class: "chips" }, r.circuits.map(chip))))) :
      el("tr", {}, el("td", { colspan: cols.length + 1, class: "muted", text: "Nothing matches these filters." })))));

  const needsGrist = roomsUI.group !== "location" && !Object.keys(state.circuit_info).length;
  $("#rooms-note").replaceChildren(needsGrist ? el("div", { class: "banner warn" },
    "No Grist circuit map loaded yet, so circuits can't be grouped by room or anchor. Set GRIST_API_KEY / GRIST_SERVER and each panel's Grist panel number.") : "");
  $("#rooms-count").textContent = `${rows.length} ${groupWord} · ${fmtA(total)} dedicated`;
  $("#rooms-table").replaceChildren(table);
}

function wireRoomsControls() {
  const g = $("#roomGroup"), q = $("#roomQ"), t = $("#roomTrips"), i = $("#roomIdle");
  g.value = roomsUI.group; q.value = roomsUI.q; t.checked = roomsUI.trips; i.checked = roomsUI.idle;
  g.addEventListener("change", () => {
    roomsUI.group = g.value;
    if (roomsUI.sort.key === "detail" && g.value !== "anchor") roomsUI.sort = { key: "dedicated", dir: -1 };
    saveRoomsUI(); render();
  });
  q.addEventListener("input", () => { roomsUI.q = q.value; saveRoomsUI(); render(); });
  t.addEventListener("change", () => { roomsUI.trips = t.checked; saveRoomsUI(); render(); });
  i.addEventListener("change", () => { roomsUI.idle = i.checked; saveRoomsUI(); render(); });
}

function render() {
  if (!state) return;
  const f = filters();
  renderStats();
  $("#view-panels").hidden = view !== "panels";
  $("#view-rooms").hidden = view !== "rooms";
  $("#estKey").hidden = view !== "panels";
  $("#estKey").replaceChildren(...(f.est
    ? ["bar = actual ÷ estimate · ", el("b", { class: "r", text: "red" }), " = 20%+ over"]
    : [`bar = load of ${CIRCUIT_W} W · `, el("b", { class: "y", text: "amber" }), ` > ${CONTINUOUS_W} W · `, el("b", { class: "r", text: "red" }), ` > ${CIRCUIT_W} W`]));
  if (view === "panels") renderPanels(f); else renderRooms(f);
}

// ---- wiring ----------------------------------------------------------------
function setConn(ok, text) {
  $("#conn").replaceChildren(el("span", { class: "dot " + (ok ? "ok" : "bad") }), text);
}
function connect() {
  const es = new EventSource("/api/stream");
  es.onmessage = (e) => { state = JSON.parse(e.data); setConn(true, "live"); render(); };
  es.onerror = () => setConn(false, "reconnecting…");
}

try {
  const saved = JSON.parse(localStorage.getItem("siq.filters") || "{}");
  if (saved.q) $("#q").value = saved.q;
  if (saved.t) $("#trippedOnly").checked = true;
  if (saved.e) $("#compareEst").checked = true;
  if (saved.view) view = saved.view;
  if (saved.p) $("#panelFilter").append(el("option", { value: saved.p, text: saved.p })), $("#panelFilter").value = saved.p;
} catch {}
// bookmarkable: /?view=rooms&group=anchor
const params = new URLSearchParams(location.search);
if (["panels", "rooms"].includes(params.get("view"))) view = params.get("view");
if (GROUPS[params.get("group")]) roomsUI.group = params.get("group");
document.querySelectorAll(".tabs button").forEach((b) => {
  b.classList.toggle("active", b.dataset.view === view);
  b.addEventListener("click", () => {
    view = b.dataset.view;
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    render();
  });
});
if (params.get("est") === "1") $("#compareEst").checked = true;
["#q", "#panelFilter", "#trippedOnly", "#compareEst"].forEach((s) => $(s).addEventListener("input", render));
setInterval(() => state && ($("#updated").textContent = "Updated " + ago(state.updated_at)), 1000);
wireRoomsControls();
connect();
