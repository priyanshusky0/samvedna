/* SAMVEDNA dashboard: vanilla JS over the JSON API. One global state object
   ({state, days}) drives every panel; changing it re-renders everything. */
"use strict";

const S = { state: "", days: 30, metric: "fires", hazard: "flood", layers: { fires: true, quakes: true, events: true } };
const charts = {};
const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const SOURCE_LABEL = { firms: "FIRMS", firms_archive: "FIRMS archive", usgs: "USGS", eonet: "EONET", openmeteo: "Open-Meteo", gdelt: "GDELT" };

async function api(path, params = {}) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined));
  const res = await fetch(`/api/${path}${q.toString() ? "?" + q : ""}`);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

function ago(ts) {
  if (!ts || ts === "None") return "never";
  const mins = Math.round((Date.now() - new Date(ts.replace(" ", "T")).getTime()) / 60000);
  if (mins < 60) return `${Math.max(mins, 0)} min ago`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h ago`;
  return `${Math.round(mins / 1440)} d ago`;
}

function sevBadge(sev) {
  return `<span class="sev ${sev}"><span class="ic"></span>${sev === "high" ? "High" : "Medium"}</span>`;
}

/* ------------------------------------------------------------- summary */
async function loadSummary() {
  try {
    const d = await api("summary", { state: S.state, days: S.days });
    $("k-fires").textContent = fmt(d.fires);
    $("k-fires-sub").textContent = `${d.fire_days_covered} of ${S.days} days with satellite data`;
    $("k-quakes").textContent = fmt(d.quakes);
    $("k-events").textContent = fmt(d.events);
    $("k-news").textContent = fmt(d.news);
    $("k-news-sub").textContent = S.state ? `headlines mentioning ${S.state}` : "GDELT, hazard queries";
    $("k-high").textContent = fmt(d.alerts.high);
    $("k-med").textContent = fmt(d.alerts.medium);
    $("freshness").innerHTML = "Last successful fetch: " + Object.entries(d.last_update)
      .map(([src, v]) => `<span><span class="dot ${v ? v.status : "none"}"></span><b>${SOURCE_LABEL[src]}</b> ${v ? ago(v.last_success) : "not yet run"}</span>`)
      .join("");
  } catch (e) { console.error(e); }
}

/* ----------------------------------------------------------------- map */
let map, stateLayer, layerGroups = {};
function initMap() {
  map = L.map("map", { zoomControl: true, preferCanvas: true }).setView([18.5, 76.5], 5);
  setTiles();
  window.addEventListener("themechange", () => { setTiles(); refreshCharts(); });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { setTiles(); refreshCharts(); });
  document.querySelectorAll(".layer-toggles input").forEach((cb) => cb.addEventListener("change", () => {
    S.layers[cb.dataset.layer] = cb.checked;
    const g = layerGroups[cb.dataset.layer];
    if (g) cb.checked ? g.addTo(map) : map.removeLayer(g);
  }));
  api("map", { layer: "states" }).then((gj) => {
    stateLayer = L.geoJSON(gj, {
      style: stateStyle,
      onEachFeature: (f, layer) => {
        layer.bindTooltip(f.properties.name, { sticky: true });
        layer.on("click", () => setState(S.state === f.properties.name ? "" : f.properties.name));
      },
    }).addTo(map);
    stateLayer.bringToBack();
  });
}
let tiles;
function isDark() {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
}
function setTiles() {
  // OpenStreetMap standard tiles (light use, attributed); darkened via CSS in dark mode.
  if (!tiles) {
    tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 12, className: "base-tiles",
    }).addTo(map);
  }
  if (stateLayer) stateLayer.setStyle(stateStyle);
}
function stateStyle(f) {
  const sel = S.state && f.properties.name === S.state;
  return { color: css("--accent"), weight: sel ? 2.5 : 1, opacity: sel ? 1 : 0.7, fillColor: css("--accent"), fillOpacity: sel ? 0.12 : S.state ? 0.02 : 0.05 };
}

async function loadMap() {
  $("map-loading").style.display = "grid";
  try {
    const [fires, quakes, events] = await Promise.all(["fires", "quakes", "events"].map((l) => api("map", { layer: l, days: S.days, state: S.state })));
    Object.values(layerGroups).forEach((g) => map.removeLayer(g));
    layerGroups = {};

    // Fire density heatmap, weighted by fire radiative power (log-scaled).
    const pts = fires.features.map((f) => [f.geometry.coordinates[1], f.geometry.coordinates[0], Math.min(1, Math.log10(1 + (f.properties.frp || 1)) / 2)]);
    layerGroups.fires = L.heatLayer(pts, { radius: 9, blur: 12, maxZoom: 9, minOpacity: 0.35,
      gradient: { 0.2: "#f6c8a8", 0.45: "#f1955f", 0.7: "#eb6834", 0.9: "#b8441b", 1: "#7f2a0c" } });

    const qg = L.layerGroup();
    quakes.features.forEach((f) => {
      const p = f.properties, [lon, lat] = f.geometry.coordinates;
      L.circleMarker([lat, lon], { radius: 3 + (p.mag - 2) * 3, color: css("--surface"), weight: 2, fillColor: css("--series-1"), fillOpacity: 0.85 })
        .bindPopup(`<b>M${fmt(p.mag, 1)}</b> · ${esc(p.place)}<br>${esc(p.time)} IST · depth ${fmt(p.depth_km, 0)} km<br><a href="${esc(p.url)}" target="_blank" rel="noopener">USGS event page</a>`)
        .addTo(qg);
    });
    layerGroups.quakes = qg;

    const eg = L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 40 });
    events.features.forEach((f) => {
      const p = f.properties, [lon, lat] = f.geometry.coordinates;
      eg.addLayer(L.circleMarker([lat, lon], { radius: 7, color: css("--surface"), weight: 2, fillColor: css("--series-3"), fillOpacity: 0.9 })
        .bindPopup(`<b>${esc(p.title)}</b><br>${esc(p.category)} · ${esc(p.time)} IST${p.url ? `<br><a href="${esc(p.url)}" target="_blank" rel="noopener">Source</a>` : ""}`));
    });
    layerGroups.events = eg;

    Object.entries(layerGroups).forEach(([k, g]) => { if (S.layers[k]) g.addTo(map); });
  } catch (e) { console.error(e); }
  $("map-loading").style.display = "none";
}

/* -------------------------------------------------------------- alerts */
async function loadAlerts() {
  const box = $("alerts");
  try {
    const rows = await api("alerts", { state: S.state, days: S.days });
    $("alerts-count").textContent = `${rows.length} in last ${S.days} d`;
    box.innerHTML = rows.length ? rows.map((a) => `
      <div class="alert ${a.severity}">
        <div class="meta">${sevBadge(a.severity)}<span>${esc(a.date)}</span><span class="pill">${esc(a.method)}</span><span>${esc(a.state)}</span></div>
        <div class="msg">${esc(a.message)}</div>
      </div>`).join("")
      : `<div class="empty">No anomalies in the last ${S.days} days${S.state ? " for " + esc(S.state) : ""}.<br>Everything is within its usual range.</div>`;
  } catch (e) { box.innerHTML = `<div class="empty">Could not load alerts.</div>`; }
}

/* ---------------------------------------------------------- timeseries */
const METRIC_TABS = [["fires", "Fires"], ["rain", "Rainfall"], ["tmax", "Max temp"], ["quakes", "Quakes"],
  ["news_flood", "News: flood"], ["news_fire", "News: fire"], ["news_heat_drought", "News: heat"], ["news_earthquake", "News: quake"]];

function baseOptions(extra = {}) {
  const grid = css("--grid"), tick = css("--muted");
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { display: false }, tooltip: { backgroundColor: css("--surface"), titleColor: css("--text"), bodyColor: css("--text-2"), borderColor: css("--border"), borderWidth: 1, padding: 8, ...extra.tooltip } },
    scales: {
      x: { grid: { display: false }, ticks: { color: tick, maxTicksLimit: 8, maxRotation: 0 }, border: { color: grid } },
      y: { grid: { color: grid }, ticks: { color: tick, maxTicksLimit: 5 }, border: { display: false }, beginAtZero: extra.beginAtZero ?? true },
    },
  };
}
const shortDate = (d) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" });

function initMetricTabs() {
  $("metric-tabs").innerHTML = METRIC_TABS.map(([k, l]) => `<button data-m="${k}" class="${k === S.metric ? "on" : ""}">${l}</button>`).join("");
  $("metric-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    S.metric = b.dataset.m;
    $("metric-tabs").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    loadTimeseries();
  });
}

async function loadTimeseries() {
  const days = Math.max(S.days, 30);
  try {
    const d = await api("timeseries", { metric: S.metric, state: S.state, days });
    $("ts-region").textContent = `· ${d.label || ""} · ${d.region}${d.unit ? " · " + d.unit : ""}`;
    $("ts-label").textContent = d.label || "Daily value";
    const pts = d.points || [];
    const labels = pts.map((p) => shortDate(p.date));
    const up = pts.map((p) => (p.mean === null || p.std === null ? null : p.mean + 2 * p.std));
    const lo = pts.map((p) => (p.mean === null || p.std === null ? null : Math.max(0, p.mean - 2 * p.std)));
    const flagColor = pts.map((p) => (p.flag === "high" ? css("--critical") : p.flag === "medium" ? css("--warning") : "transparent"));
    const ds = [
      { label: "+2σ", data: up, borderWidth: 0, pointRadius: 0, fill: "+1", backgroundColor: css("--band"), spanGaps: false },
      { label: "−2σ", data: lo, borderWidth: 0, pointRadius: 0, fill: false, spanGaps: false },
      { label: "30-day mean", data: pts.map((p) => p.mean), borderColor: css("--baseline"), borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0, spanGaps: false },
      { label: d.label || "Value", data: pts.map((p) => p.value), borderColor: css("--series-1"), borderWidth: 2, tension: 0.2, spanGaps: false,
        pointRadius: pts.map((p) => (p.flag ? 5 : 0)), pointHoverRadius: 5, pointBackgroundColor: flagColor, pointBorderColor: css("--surface"), pointBorderWidth: 2 },
    ];
    const opts = baseOptions({ beginAtZero: S.metric !== "tmax", tooltip: {
      filter: (item) => item.datasetIndex >= 2,
      callbacks: { afterBody: (items) => { const p = pts[items[0].dataIndex]; return p && p.z !== null ? [`z = ${fmt(p.z, 1)}${p.flag ? " (" + p.flag + ")" : ""}`] : []; } },
    } });
    if (charts.ts) charts.ts.destroy();
    charts.ts = new Chart($("ts-chart"), { type: "line", data: { labels, datasets: ds }, options: opts });
    const missing = pts.filter((p) => p.value === null).length;
    const notes = [];
    if (d.national_only && S.state) notes.push("News timelines are national (GDELT): the state filter does not apply to this metric.");
    if (missing) notes.push(`${missing} day(s) have no data (gaps shown as breaks, never filled with zero).`);
    if (!pts.length) notes.push("No data yet for this metric.");
    $("ts-note").textContent = notes.join(" ");
  } catch (e) { console.error(e); $("ts-note").textContent = "Could not load series."; }
}

/* ---------------------------------------------------------------- news */
async function loadNews() {
  try {
    const d = await api("news", { hazard: S.hazard, state: S.state, days: Math.max(S.days, 30) });
    $("news-n").textContent = `${fmt(d.n_articles)} articles${S.state ? " mentioning " + S.state : ""} · mean headline sentiment ${fmt(d.mean_headline_sentiment, 2)}`;
    const ts = d.tone_series || [];
    const labels = ts.map((t) => shortDate(t.date));
    $("hz-label").textContent = `${d.hazard_metric.label} (${d.hazard_metric.region}, ${d.hazard_metric.unit})`;
    if (charts.vol) charts.vol.destroy();
    charts.vol = new Chart($("news-vol"), { type: "bar", data: { labels, datasets: [{ label: "Articles", data: ts.map((t) => t.volume), backgroundColor: css("--series-1"), borderRadius: 2, categoryPercentage: 0.9, barPercentage: 0.9 }] }, options: baseOptions() });
    if (charts.hz) charts.hz.destroy();
    charts.hz = new Chart($("news-hz"), { type: "line", data: { labels, datasets: [{ label: d.hazard_metric.label, data: ts.map((t) => t.hazard_value), borderColor: css("--series-2"), borderWidth: 2, pointRadius: 0, tension: 0.2, spanGaps: false }] }, options: baseOptions({ beginAtZero: d.hazard_metric.key !== "tmax" }) });
    if (charts.tone) charts.tone.destroy();
    charts.tone = new Chart($("news-tone"), { type: "line", data: { labels, datasets: [
      { label: "GDELT tone", data: ts.map((t) => t.gdelt_tone), borderColor: css("--series-1"), borderWidth: 2, pointRadius: 0, tension: 0.2 },
      { label: "Headline sentiment ×10", data: ts.map((t) => (t.headline_sentiment === null ? null : t.headline_sentiment * 10)), borderColor: css("--series-3"), borderWidth: 2, pointRadius: 0, tension: 0.2, spanGaps: true },
    ] }, options: baseOptions({ beginAtZero: false }) });

    const kws = d.keywords || [];
    const maxW = Math.max(...kws.map((k) => k.weight), 1);
    $("keywords").innerHTML = kws.length ? kws.slice(0, 12).map((k) => `<div class="kw"><span>${esc(k.term)}</span><div class="bar" style="width:${(k.weight / maxW) * 100}%" title="${fmt(k.weight, 2)}"></div></div>`).join("") : `<div class="empty">Not enough headlines yet.</div>`;
    $("topics").innerHTML = (d.topics || []).length ? d.topics.map((t) => `
      <div class="topic"><div class="terms">${t.terms.slice(0, 5).map(esc).join(" · ")}</div>
      <div class="share">${fmt(t.share * 100)}% of headlines</div>
      <ul>${t.examples.map((x) => `<li><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a></li>`).join("")}</ul></div>`).join("")
      : `<div class="empty">Topic model needs more headlines${S.state ? " for this state (≥ 40)" : ""}.</div>`;
    $("headlines").innerHTML = (d.articles || []).length ? d.articles.map((a) => `<li><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a>
      <div class="meta">${esc(a.domain)} · ${esc(a.seen_at.slice(0, 16))} IST${a.state ? " · " + esc(a.state) : ""}</div></li>`).join("") : `<li class="empty">No headlines in this window.</li>`;
    $("weekly").innerHTML = (d.weekly_keywords || []).slice().reverse().map((w) => `<div><b>w/c ${shortDate(w.week_start)}</b> <span class="muted">(${w.n_articles})</span> — ${w.keywords.map(esc).join(", ")}</div>`).join("") || `<div class="empty">–</div>`;
    const sc = Object.entries(d.state_counts || {});
    $("state-counts").innerHTML = sc.length ? sc.map(([k, v]) => `<span class="pill">${esc(k)}: ${v}</span>`).join(" ") : "<span class='muted'>No state mentions found.</span>";
  } catch (e) { console.error(e); $("news-n").textContent = "Could not load news."; }
}

/* --------------------------------------------------------------- stats */
async function loadStats() {
  const [tr, co, se] = await Promise.all([api("trends"), api("correlation"), api("seasonal")]);
  const t = (tr.results || []).filter((r) => !S.state || r.region === S.state || r.region === "All states" || r.region === "India")
    .sort((a, b) => a.p_value - b.p_value);
  $("trends-table").innerHTML = `<thead><tr><th>Metric</th><th>Region</th><th>Trend</th><th class="n">Sen's slope</th><th class="n">τ</th><th class="n">p</th><th>Weeks</th></tr></thead><tbody>` +
    (t.map((r) => `<tr><td>${esc(r.label)}</td><td>${esc(r.region)}</td><td><span class="arrow">${r.trend === "increasing" ? "↑" : r.trend === "decreasing" ? "↓" : "–"}</span> ${esc(r.trend)}</td>
      <td class="n">${fmt(r.sens_slope, 2)} <span class="muted small">${esc(r.unit)}</span></td><td class="n">${fmt(r.tau, 2)}</td><td class="n">${r.p_value < 0.001 ? "&lt;0.001" : fmt(r.p_value, 3)}</td>
      <td class="small">${r.n_weeks} (${esc(r.start)} → ${esc(r.end)})</td></tr>`).join("") || `<tr><td colspan="7" class="empty">Not computed yet.</td></tr>`) + "</tbody>";
  $("corr-table").innerHTML = `<thead><tr><th>Pair</th><th class="n">lag 0</th><th class="n">lag 1</th><th class="n">lag 2</th><th class="n">lag 3</th><th>Strongest</th><th class="n">n</th></tr></thead><tbody>` +
    ((co.results || []).map((r) => `<tr><td>${esc(r.pair)}</td>${r.lags.map((l) => `<td class="n">${l.rho === null ? "–" : fmt(l.rho, 2)}${l.p_value !== null && l.p_value < 0.05 ? "*" : ""}</td>`).join("")}
      <td>${r.best_lag === null ? "insufficient overlap" : `ρ = ${fmt(r.best_rho, 2)} at ${r.best_lag} d`}</td><td class="n">${r.n}</td></tr>`).join("") || `<tr><td colspan="7" class="empty">Not computed yet.</td></tr>`) + "</tbody>";
  const s = (se.results || []).filter((r) => (S.state ? r.region === S.state : r.region === "All states") || r.region === "All states");
  $("seasonal-table").innerHTML = `<thead><tr><th>Metric</th><th>Region</th><th>This year</th><th class="n">Value</th><th>Last year</th><th class="n">Value</th><th class="n">Change</th></tr></thead><tbody>` +
    (s.map((r) => `<tr><td>${esc(r.label)}</td><td>${esc(r.region)}</td><td class="small">${esc(r.current_period)}</td><td class="n">${fmt(r.current, 1)}</td><td class="small">${esc(r.last_year_period)}</td><td class="n">${fmt(r.last_year, 1)}</td>
      <td class="n">${r.change_pct === null ? "–" : (r.change_pct > 0 ? "+" : "") + fmt(r.change_pct, 0) + "%"}</td></tr>`).join("") || `<tr><td colspan="7" class="empty">Needs data from the same days last year.</td></tr>`) + "</tbody>";
}

async function loadHealth() {
  const d = await api("health");
  $("health-table").innerHTML = `<thead><tr><th>Source</th><th>Status</th><th>Last run</th><th>Last success</th><th class="n">Fetched</th><th class="n">New rows</th><th class="n">Rejected</th><th>Rejection reasons</th></tr></thead><tbody>` +
    Object.entries(d.sources).map(([src, v]) => v ? `<tr><td>${SOURCE_LABEL[src]}</td><td><span class="dot ${v.status}"></span>${esc(v.status)}</td><td class="small">${esc(v.finished_at.slice(0, 16))}</td><td class="small">${ago(v.last_success)}</td>
      <td class="n">${fmt(v.fetched)}</td><td class="n">${fmt(v.inserted)}</td><td class="n">${fmt(v.rejected)}</td>
      <td class="small">${Object.entries(v.rejected_reasons).map(([k, n]) => `${esc(k)}: ${fmt(n)}`).join(", ") || "–"}${v.error ? `<div style="color:var(--critical)">${esc(v.error.slice(0, 160))}</div>` : ""}</td></tr>`
      : `<tr><td>${SOURCE_LABEL[src]}</td><td colspan="7" class="muted">not run yet</td></tr>`).join("") + "</tbody>";
  $("health-note").textContent = d.firms_key_configured ? "FIRMS: area API with MAP_KEY." : "FIRMS: no MAP_KEY configured, so the public 7-day CSV is used. Add FIRMS_MAP_KEY for a 90-day backfill.";
}

/* -------------------------------------------------------------- wiring */
function setState(st) {
  S.state = st;
  $("state-select").value = st;
  $("state-chip").classList.toggle("on", !!st);
  $("state-chip-label").textContent = st;
  if (stateLayer) {
    stateLayer.setStyle(stateStyle);
    if (st) stateLayer.eachLayer((l) => { if (l.feature.properties.name === st) map.fitBounds(l.getBounds(), { padding: [20, 20] }); });
    else map.setView([18.5, 76.5], 5);
  }
  refresh();
}
function refreshCharts() { loadTimeseries(); loadNews(); }
let activePanel = "news";
function refresh() {
  loadSummary(); loadMap(); loadAlerts(); loadTimeseries(); loadNews();
  if (activePanel === "stats") loadStats();
  if (activePanel === "health") loadHealth();
}

document.addEventListener("DOMContentLoaded", () => {
  initMap(); initMetricTabs();
  $("state-select").addEventListener("change", (e) => setState(e.target.value));
  $("state-clear").addEventListener("click", () => setState(""));
  $("days-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    S.days = +b.dataset.days;
    $("days-seg").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    refresh();
  });
  $("hazard-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    S.hazard = b.dataset.hz;
    $("hazard-tabs").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    loadNews();
  });
  document.querySelector(".tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    activePanel = b.dataset.panel;
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("on", p.id === "panel-" + activePanel));
    if (activePanel === "stats") loadStats();
    if (activePanel === "health") loadHealth();
  });
  refresh();
  setInterval(loadSummary, 5 * 60 * 1000);
});
