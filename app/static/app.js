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
  try { localStorage.setItem("siq.filters", JSON.stringify({ q: $("#q").value, p: $("#panelFilter").value, t: $("#trippedOnly").checked, view })); } catch {}
  return { q: $("#q").value.trim().toLowerCase(), panel: $("#panelFilter").value, tripped: $("#trippedOnly").checked };
}
function info(c) { return (c.circuit && state.circuit_info[c.circuit]) || null; }
function matches(c, panel, f) {
  if (f.tripped && c.status !== "tripped") return false;
  if (!f.q) return true;
  const i = info(c);
  const hay = [c.circuit, "slot " + c.slot, panel.name, panel.room, i && i.rooms.join(" "), i && i.load_types]
    .filter(Boolean).join(" ").toLowerCase();
  return hay.includes(f.q);
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
  for (const p of state.panels) {
    if (p.panel.enabled && p.polled && !p.online)
      banners.push(el("div", { class: "banner warn" }, `${p.panel.name} (${p.panel.ip}) is unreachable: ${p.last_error || ""} — last seen ${ago(p.last_ok)}`));
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
    rooms && `Rooms: ${rooms}`, i && i.load_types && `Loads: ${i.load_types}`, i && `Fixtures: ${i.fixture_count}`,
    c.pole_of && `Pole 2 of slot ${c.pole_of}`, c.breaker_type && c.breaker_type !== "None" && c.breaker_type,
    ...(c.errors || []),
  ].filter(Boolean).join("\n");
  const amps = c.status === "tripped" ? "TRIP" : c.status === "empty" ? "—" : c.status === "offline" ? "?"
    : c.pole_of ? "⇡" : fmtA(c.current_a);
  // like a printed panel schedule: circuit numbers sit next to the centre bus, descriptions on the outside
  const parts = [el("span", { class: "room", text: rooms }), el("span", { class: "amps", text: amps }),
    el("span", { class: "id", text: c.circuit || "S" + c.slot })];
  return el("div", { class: `slot ${side} ${c.status}${matches(c, p.panel, f) ? "" : " dim"}`, title: tip },
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
    return el("article", { class: "card" + (pn.enabled && p.polled && !p.online ? " offline" : "") },
      el("div", { class: "card-head" },
        el("h2", {}, el("span", { class: "dot " + status[0], title: status[1] }), pn.name),
        el("span", { class: "meta", text: meta }),
        el("span", { class: "totals" }, fmtA(s.total_a), " · ", fmtW(s.total_w),
          s.counts.tripped ? el("span", { class: "trip", text: `${s.counts.tripped} tripped` }) : null)),
      errs.length && p.online ? el("details", { class: "card-errors" }, el("summary", { text: `${errs.length} panel alert(s)` }),
        el("div", { text: errs.join(" · ") })) : null,
      panelSchedule(p, f));
  }));
}

// ---- rooms view ------------------------------------------------------------
function renderRooms(f) {
  const rooms = new Map();
  const room = (name) => {
    if (!rooms.has(name)) rooms.set(name, { name, dedicated: 0, shared: 0, circuits: [], tripped: 0, on: 0 });
    return rooms.get(name);
  };
  for (const p of state.panels) {
    if (f.panel && p.panel.ip !== f.panel) continue;
    for (const c of p.circuits) {
      if (c.status === "empty" || c.pole_of || !matches(c, p.panel, f)) continue;
      const i = info(c);
      const names = i && i.rooms.length ? i.rooms : ["(not in Grist schedule)"];
      const shared = names.length > 1;
      for (const n of names) {
        const r = room(n);
        r[shared ? "shared" : "dedicated"] += c.current_a;
        r.circuits.push({ c, shared });
        if (c.status === "tripped") r.tripped++;
        if (c.status === "on") r.on++;
      }
    }
  }
  const rows = [...rooms.values()].sort((a, b) => b.tripped - a.tripped || (b.dedicated + b.shared) - (a.dedicated + a.shared) || a.name.localeCompare(b.name));
  const chip = ({ c, shared }) => el("span", {
    class: `chip ${c.status}${shared ? " shared" : ""}`,
    title: `${c.circuit || "slot " + c.slot}: ${c.status}, ${c.current_a.toFixed(2)} A${shared ? " (shared with other rooms)" : ""}`,
    text: `${c.circuit || "S" + c.slot} ${c.status === "tripped" ? "TRIP" : c.status === "offline" ? "?" : c.current_a.toFixed(1)}`,
  });
  const roomTable = el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, el("th", { text: "Served room" }), el("th", { class: "num", text: "Dedicated" }),
      el("th", { class: "num", text: "+ Shared" }), el("th", { class: "num", text: "On" }), el("th", { class: "num", text: "Tripped" }),
      el("th", { text: "Circuits (dashed = shared with other rooms)" }))),
    el("tbody", {}, rows.length ? rows.map((r) => el("tr", {},
      el("td", { text: r.name }), el("td", { class: "num", text: fmtA(r.dedicated) }),
      el("td", { class: "num muted", text: r.shared ? fmtA(r.shared) : "—" }),
      el("td", { class: "num", text: r.on }), el("td", { class: "num", text: r.tripped || "—" }),
      el("td", {}, el("div", { class: "chips" }, r.circuits.map(chip))))) :
      el("tr", {}, el("td", { colspan: 6, class: "muted", text: "No circuits match." })))));

  const loc = new Map();
  for (const p of state.panels) {
    if (f.panel && p.panel.ip !== f.panel) continue;
    const k = p.panel.room || "(no room set)";
    if (!loc.has(k)) loc.set(k, { name: k, panels: [], a: 0, w: 0, tripped: 0, on: 0 });
    const r = loc.get(k);
    r.panels.push(p.panel.name); r.a += p.summary.total_a; r.w += p.summary.total_w;
    r.tripped += p.summary.counts.tripped; r.on += p.summary.counts.on;
  }
  const locTable = el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, el("th", { text: "Panel location" }), el("th", { text: "Panels" }), el("th", { class: "num", text: "Current" }),
      el("th", { class: "num", text: "Power" }), el("th", { class: "num", text: "On" }), el("th", { class: "num", text: "Tripped" }))),
    el("tbody", {}, [...loc.values()].map((r) => el("tr", {}, el("td", { text: r.name }), el("td", { text: r.panels.join(", ") }),
      el("td", { class: "num", text: fmtA(r.a) }), el("td", { class: "num", text: fmtW(r.w) }),
      el("td", { class: "num", text: r.on }), el("td", { class: "num", text: r.tripped || "—" }))))));

  const note = Object.keys(state.circuit_info).length ? null :
    el("div", { class: "banner warn" }, "No Grist circuit→room map loaded yet, so circuits can't be grouped by served room. Set GRIST_API_KEY / GRIST_SERVER and grist_panel per panel.");
  $("#view-rooms").replaceChildren(note, el("h3", { class: "section", text: "By served room (from Grist panel schedule)" }), roomTable,
    el("h3", { class: "section", text: "By panel location" }), locTable);
}

function render() {
  if (!state) return;
  const f = filters();
  renderStats();
  $("#view-panels").hidden = view !== "panels";
  $("#view-rooms").hidden = view !== "rooms";
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
  if (saved.view) view = saved.view;
  if (saved.p) $("#panelFilter").append(el("option", { value: saved.p, text: saved.p })), $("#panelFilter").value = saved.p;
} catch {}
document.querySelectorAll(".tabs button").forEach((b) => {
  b.classList.toggle("active", b.dataset.view === view);
  b.addEventListener("click", () => {
    view = b.dataset.view;
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    render();
  });
});
["#q", "#panelFilter", "#trippedOnly"].forEach((s) => $(s).addEventListener("input", render));
setInterval(() => state && ($("#updated").textContent = "Updated " + ago(state.updated_at)), 1000);
connect();
