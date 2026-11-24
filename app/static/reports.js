"use strict";
// Reports page: pick a timeframe, download the PDF. Times in the PDF use this browser's time zone.

const TIMEFRAMES = [[1, "Last hour"], [6, "Last 6 hours"], [12, "Last 12 hours"], [24, "Last 24 hours"],
  [48, "Last 2 days"], [72, "Last 3 days"], [168, "Last 7 days"], [720, "Last 30 days"]];

let tz = "";
try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch {}
let saved = 24;
try { saved = Number(localStorage.getItem("siq.reportHours")) || 24; } catch {}

document.querySelectorAll("select.hours").forEach((sel) => {
  for (const [h, label] of TIMEFRAMES) sel.append(new Option(label, String(h), false, h === saved));
  sel.addEventListener("change", () => { try { localStorage.setItem("siq.reportHours", sel.value); } catch {} });
});

document.querySelectorAll("[data-report]").forEach((btn) => btn.addEventListener("click", () => {
  const params = new URLSearchParams();
  if (tz) params.set("tz", tz);
  const sel = btn.closest(".report-card").querySelector("select.hours");
  if (sel) params.set("hours", sel.value);
  // the server names the file (e.g. SHLPanelChanges_20261124_1434.pdf) via Content-Disposition
  location.href = `/api/${btn.dataset.report}.pdf?${params}`;
}));
