/* SafeHer demo web app — vanilla JS + MapLibre. Talks to the FastAPI backend in backend/safeher/api.py. */
"use strict";

const COLORS = { high: "#1F9E89", mid: "#E0A526", low: "#C2577A", none: "#9AA0A6", accent: "#F2A541", primary: "#5B2A86" };
const KIND_STYLE = {
  police: { icon: "local_police", color: "#1A73E8", label: "Police station" },
  toilets: { icon: "wc", color: "#12857A", label: "Washroom" },
  hospital: { icon: "local_hospital", color: "#D93025", label: "Hospital" },
  pharmacy: { icon: "local_pharmacy", color: "#188038", label: "Pharmacy" },
  open_places: { icon: "storefront", color: "#B06000", label: "Open now" },
  clinic: { icon: "medical_services", color: "#D93025", label: "Clinic" },
  fuel: { icon: "local_gas_station", color: "#5F6368", label: "Fuel station" },
  convenience: { icon: "local_convenience_store", color: "#B06000", label: "Convenience store" },
  supermarket: { icon: "local_grocery_store", color: "#B06000", label: "Supermarket" },
  mall: { icon: "local_mall", color: "#B06000", label: "Mall" },
  cafe: { icon: "local_cafe", color: "#B06000", label: "Café" },
  restaurant: { icon: "restaurant", color: "#B06000", label: "Restaurant" },
};
const REPORT_CATS = {
  broken_light: { icon: "lightbulb", label: "Broken / no streetlight" },
  harassment: { icon: "front_hand", label: "Harassment experienced" },
  unsafe_crowd: { icon: "groups", label: "Unsafe crowd / loitering" },
  isolated_stretch: { icon: "do_not_step", label: "Isolated stretch" },
  poor_footpath: { icon: "construction", label: "Poor footpath" },
  unsafe_washroom: { icon: "wc", label: "Unsafe washroom" },
  positive: { icon: "thumb_up", label: "Well-lit / helpful place" },
};
const SIGNAL_ICONS = { lighting: "light", transit: "directions_bus", safe_spaces: "storefront", incidents: "history", reports: "campaign" };

const state = {
  areas: [], meta: null, area: null,
  origin: null, dest: null,          // {name, lat, lon}
  departMin: null, isNow: true,      // minutes since local midnight
  plan: null, selected: 0,
  mode: "routes",                    // routes | nearby | report-pick | report-place
  nearby: null, nearbyKind: null,
  reportCat: null, reportPoint: null,
  themePref: "auto",                 // auto | light | dark
  dark: false,
  markers: { origin: null, dest: null, labels: [], pois: [], reports: [], temp: null },
  planSeq: 0, pendingFit: false,
  lang: "en", explanation: null, explainSeq: 0,   // explanation = {key, lang, data | null, error?}
};

/* ---------- small helpers ---------- */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (name, cls = "") => `<span class="msr ${cls}">${name}</span>`;
const fmtDist = (m) => (m < 1000 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(1)} km`);
const fmtDur = (min) => (min < 60 ? `${min} min` : `${Math.floor(min / 60)} hr ${min % 60} min`);
const bandKey = (score) => (score == null ? "none" : score >= 75 ? "high" : score >= 50 ? "mid" : "low");
const bandColor = (score) => COLORS[bandKey(score)];

function fmtClock(min) {
  const h = Math.floor(min / 60) % 24, m = min % 60;
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}
function nowParts() {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: state.area?.timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date()).map((x) => [x.type, x.value]));
  return { date: `${p.year}-${p.month}-${p.day}`, min: Number(p.hour) * 60 + Number(p.minute) };
}
function departIso() {
  const { date, min } = nowParts();
  const m = state.isNow ? min : state.departMin;
  return `${date}T${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}:00`;
}
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`);
  return body;
}
let toastTimer;
function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), ms);
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const isPhone = () => matchMedia("(max-width: 760px)").matches;

/* ---------- map ---------- */
let map;

function initMap(center) {
  map = new maplibregl.Map({
    container: "map", style: basemapStyle(false), center: [center[1], center[0]], zoom: 14.2,
    attributionControl: { compact: true }, dragRotate: false, pitchWithRotate: false,
  });
  map.touchZoomRotate.disableRotation();
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
  map.on("style.load", addLayers);
  map.on("click", onMapClick);
  map.on("moveend", updateGoogleAttribution);
  map.on("zoomend", () => drawLabels());
  // If the page was laid out while hidden (e.g. a background tab), the canvas starts at 400×300.
  new ResizeObserver(() => map.resize()).observe(document.getElementById("map"));
  map.once("load", () => map.resize());
  map.on("mouseenter", "alt-line", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "alt-line", () => (map.getCanvas().style.cursor = ""));
  return new Promise((r) => map.once("load", r));
}

function addLayers() {
  const empty = { type: "FeatureCollection", features: [] };
  const add = (id, src) => { if (!map.getSource(id)) map.addSource(id, { type: "geojson", data: src || empty }); };
  ["overlays", "alt-routes", "pieces", "weak"].forEach((id) => add(id));

  // Street lamps glow at night; police & washrooms as small dots when zoomed in.
  map.addLayer({ id: "lamps", type: "circle", source: "overlays", filter: ["==", ["get", "kind"], "street_lamp"],
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 1.5, 17, 4], "circle-color": COLORS.accent,
      "circle-opacity": state.dark ? 0.85 : 0.0, "circle-blur": 0.4 } });

  map.addLayer({ id: "alt-casing", type: "line", source: "alt-routes", layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": state.dark ? "#4A4A52" : "#7C8AA0", "line-width": 9 } });
  map.addLayer({ id: "alt-line", type: "line", source: "alt-routes", layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": state.dark ? "#7B7B86" : "#B9C3D3", "line-width": 6 } });

  map.addLayer({ id: "lit-glow", type: "line", source: "pieces", filter: [">=", ["coalesce", ["get", "light"], -1], 70],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": COLORS.accent, "line-width": 20, "line-blur": 10, "line-opacity": state.dark ? 0.55 : 0 } });
  map.addLayer({ id: "sel-casing", type: "line", source: "pieces", layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": state.dark ? "#0B0B0D" : "#FFFFFF", "line-width": 11 } });
  map.addLayer({ id: "sel-line", type: "line", source: "pieces", layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-width": 7, "line-color": ["case", ["==", ["get", "score"], null], COLORS.none,
      ["interpolate", ["linear"], ["get", "score"], 40, COLORS.low, 60, COLORS.mid, 80, COLORS.high]] } });
  map.addLayer({ id: "weak-line", type: "line", source: "weak", layout: { "line-cap": "round" },
    paint: { "line-color": COLORS.low, "line-width": 3, "line-dasharray": [1, 1.6], "line-offset": 0 } });

  loadOverlays();
  drawRoutes(state.pendingFit);
}

async function loadOverlays() {
  if (!state.area || !map.getSource("overlays")) return;
  try { map.getSource("overlays").setData(await api(`/api/layers/${state.area.id}`)); } catch { /* overlays are optional */ }
}

/* Google mode: Google's own map tiles, so Google Places/Routes content may be shown on it. */
const googleOn = (feature) => Boolean(state.meta?.google?.[feature]);

function basemapStyle(dark) {
  if (!googleOn("map_tiles")) return dark ? state.meta.basemaps.dark : state.meta.basemaps.light;
  const style = dark ? "dark" : "light";
  return {
    version: 8,
    sources: { google: { type: "raster", tileSize: 256, maxzoom: 22,
      tiles: [`${location.origin}/api/google/tiles/${style}/{z}/{x}/{y}`] } },
    layers: [{ id: "google-basemap", type: "raster", source: "google" }],
  };
}

const updateGoogleAttribution = debounce(async () => {
  const el = $("#googleAttribution");
  if (!el || !googleOn("map_tiles") || !map) return;
  const b = map.getBounds();
  const q = new URLSearchParams({ style: state.dark ? "dark" : "light", zoom: String(Math.round(map.getZoom())),
    north: b.getNorth().toFixed(5), south: b.getSouth().toFixed(5), east: b.getEast().toFixed(5), west: b.getWest().toFixed(5) });
  try { el.textContent = (await api(`/api/google/attribution?${q}`)).copyright || "Map data © Google"; }
  catch { el.textContent = "Map data © Google"; }
}, 400);

function setDark(dark) {
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  if (dark === state.dark) return;
  state.dark = dark;
  map.setStyle(basemapStyle(dark), { diff: false }); // style.load re-adds our layers
  updateGoogleAttribution();
}

function applyTheme() {
  const night = state.plan ? state.plan.night_fraction >= 0.5 : false;
  setDark(state.themePref === "dark" || (state.themePref === "auto" && night));
  const labels = { auto: "auto (follows departure time)", light: "light", dark: "dark" };
  $("#themeBtn").setAttribute("aria-label", `Map theme: ${labels[state.themePref]}`);
  $("#themeBtn").title = `Map theme: ${labels[state.themePref]}`;
  $("#themeBtn .msr").textContent = { auto: "contrast", light: "light_mode", dark: "dark_mode" }[state.themePref];
}

/* ---------- markers ---------- */
function destPinEl() {
  const el = document.createElement("div");
  el.className = "pin-dest";
  el.innerHTML = `<svg viewBox="0 0 30 40"><path d="M15 1C7.3 1 1 7.1 1 14.7 1 25 15 39 15 39s14-14 14-24.3C29 7.1 22.7 1 15 1z" fill="${COLORS.low}" stroke="#fff" stroke-width="2"/><circle cx="15" cy="14.5" r="5" fill="#fff"/></svg>`;
  return el;
}
function placeEndpoint(which) {
  const p = state[which];
  const key = which === "origin" ? "origin" : "dest";
  if (state.markers[key]) state.markers[key].remove();
  state.markers[key] = null;
  if (!p) return;
  const el = which === "origin" ? Object.assign(document.createElement("div"), { className: "pin-origin" }) : destPinEl();
  el.setAttribute("aria-label", which === "origin" ? "Start (drag to move)" : "Destination (drag to move)");
  const m = new maplibregl.Marker({ element: el, draggable: true, anchor: which === "origin" ? "center" : "bottom" })
    .setLngLat([p.lon, p.lat]).addTo(map);
  m.on("dragend", () => {
    const ll = m.getLngLat();
    setPoint(which, { name: "Dropped pin", lat: ll.lat, lon: ll.lng });
    planTrip();
  });
  state.markers[key] = m;
}
function clearMarkers(kind) { state.markers[kind].forEach((m) => m.remove()); state.markers[kind] = []; }

function poiMarker(p, onClick) {
  const st = KIND_STYLE[p.kind] || KIND_STYLE.open_places;
  const el = document.createElement("button");
  el.className = "poi-pin"; el.style.background = st.color; el.innerHTML = icon(st.icon);
  el.setAttribute("aria-label", `${p.name || st.label}`);
  el.addEventListener("click", (e) => { e.stopPropagation(); onClick?.(); });
  return new maplibregl.Marker({ element: el }).setLngLat([p.lon, p.lat]).addTo(map);
}

/* ---------- endpoints & search ---------- */
function setPoint(which, p) {
  state[which] = p;
  $(which === "origin" ? "#originInput" : "#destInput").value = p ? p.name : "";
  placeEndpoint(which);
}

function setupSearch(inputSel, listSel, which) {
  const input = $(inputSel), list = $(listSel);
  let items = [], active = -1, empty = "";
  const render = () => {
    if (empty) {
      list.innerHTML = `<li class="suggest-empty">${icon("search_off")}<div><b>No matches for “${esc(empty)}”</b>
        <small>SafeHer has data for ${esc(state.areas.map((a) => `${a.name} (${a.city})`).join(" and "))} only.</small></div></li>`;
      return;
    }
    const glyph = (r) => (r.source === "google" ? "travel_explore" : r.kind === "landmark" ? "location_on"
      : r.kind === "street" ? "add_road" : "place");
    list.innerHTML = items.map((r, i) => `<li role="option" data-i="${i}" aria-selected="${i === active}">
      ${icon(glyph(r))}<div><b>${esc(r.name)}</b><small>${esc(r.detail)}${r.other_area ? " · switches city" : ""}</small></div></li>`).join("")
      + (items.some((r) => r.source === "google") ? `<li class="powered-by" aria-hidden="true">Search results include Google Maps</li>` : "");
  };
  let session = null; // Google Autocomplete session: one per search, ended by a Place Details call
  const choose = async (r) => {
    items = []; empty = ""; render(); input.blur();
    if (r.place_id) {
      try {
        const p = await api(`/api/google/place/${encodeURIComponent(r.place_id)}?session=${session || ""}`);
        r = { ...r, lat: p.lat, lon: p.lon, area: p.area };
      } catch (err) { toast(err.message); return; } finally { session = null; }
    }
    if (r.area && r.area !== state.area.id && state.areas.some((a) => a.id === r.area)) useArea(r.area);
    setPoint(which, { name: r.name, lat: r.lat, lon: r.lon });
    planTrip(true);
  };
  const landmarkItems = () => state.area.landmarks.map((l) => ({ ...l, kind: "landmark", detail: `landmark · ${state.area.city}` }));
  const query = debounce(async () => {
    const q = input.value.trim();
    if (!q) { items = landmarkItems(); active = -1; empty = ""; return render(); }
    if (googleOn("autocomplete") && !session) session = crypto.randomUUID?.() || String(Date.now());
    const s = googleOn("autocomplete") ? `&session=${session}` : "";
    const c = map ? map.getCenter() : null;
    const near = c ? `&lat=${c.lat.toFixed(5)}&lon=${c.lng.toFixed(5)}` : "";
    try { items = await api(`/api/search?q=${encodeURIComponent(q)}&area=${state.area.id}${s}${near}`); } catch { items = []; }
    empty = items.length ? "" : q;
    active = items.length ? 0 : -1; render();
  }, 120);
  input.addEventListener("focus", () => { input.select(); items = landmarkItems(); active = -1; empty = ""; render(); });
  input.addEventListener("input", query);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { active = Math.min(items.length - 1, active + 1); render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); render(); e.preventDefault(); }
    else if (e.key === "Enter" && items[Math.max(active, 0)]) { choose(items[Math.max(active, 0)]); }
    else if (e.key === "Escape") { items = []; render(); input.blur(); }
  });
  input.addEventListener("blur", () => setTimeout(() => { items = []; empty = ""; render(); if (state[which]) input.value = state[which].name; }, 150));
  list.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li[data-i]"); if (li) { e.preventDefault(); choose(items[Number(li.dataset.i)]); }
  });
}

/* ---------- planning ---------- */
/* ---------- go anywhere: first trip in a new place downloads its street data ---------- */
function showPreparing(resp, seq, fit) {
  state.plan = null;
  const job = resp.job;
  if (!state.prepStarted || state.prepArea !== job.area) { state.prepStarted = Date.now(); state.prepArea = job.area; }
  // Google's route straight away (unscored), so there's something to look at.
  if (map.getSource("alt-routes")) {
    const line = (coords) => ({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords.map(([a, b]) => [b, a]) } });
    map.getSource("alt-routes").setData({ type: "FeatureCollection", features: (resp.preview || []).map((r) => line(r.geometry)) });
    ["pieces", "weak"].forEach((id) => map.getSource(id).setData({ type: "FeatureCollection", features: [] }));
    clearMarkers("labels"); clearMarkers("pois");
    const pts = (resp.preview || []).flatMap((r) => r.geometry);
    if (fit) fitTo(pts.length ? pts : [[state.origin.lat, state.origin.lon], [state.dest.lat, state.dest.lon]]);
  }
  const secs = Math.round((Date.now() - state.prepStarted) / 1000);
  const failed = job.state === "failed";
  $("#view").innerHTML = `<section class="prep-card${failed ? " failed" : ""}">
      <div class="prep-head">${failed ? icon("error") : '<span class="spinner" aria-hidden="true"></span>'}
        <b>${failed ? "Couldn’t get street data here" : "First trip here: getting street data"}</b></div>
      <p>${esc(job.message)}</p>
      ${failed ? "" : `<p class="muted small">SafeHer is downloading streets, lighting and open places for this area from OpenStreetMap so it can score routes. This happens once per area and usually takes 1–5 minutes${secs > 5 ? ` · ${secs}s so far` : ""}.</p>`}
      ${(resp.preview || []).map((r) => `<div class="prep-preview">${icon("map")}<span><b>${esc(r.label)}</b> · ${fmtDist(r.distance_m)} · ${fmtDur(r.duration_min)}</span><span class="badge band-none">Not scored yet</span></div>`).join("")}
      ${failed ? `<button class="btn" id="prepRetry">${icon("refresh")}Try again</button>` : ""}
    </section>`;
  $("#prepRetry")?.addEventListener("click", () => { state.prepStarted = null; planTrip(true); });
  if (failed) return;
  clearTimeout(state.prepTimer);
  state.prepTimer = setTimeout(async () => {
    if (seq !== state.planSeq) return; // the user changed the trip meanwhile
    try {
      const st = await api(`/api/prepare/${job.area}`);
      if (seq !== state.planSeq) return;
      if (st.state === "ready") { state.prepStarted = null; planTrip(true); }
      else showPreparing({ ...resp, job: st }, seq, false);
    } catch { showPreparing(resp, seq, false); }
  }, resp.poll_ms || 2000);
}

async function planTrip(fit = false) {
  if (!state.origin || !state.dest) return;
  state.mode = "routes"; setChip(null); clearMarkers("pois"); hideBanner();
  const seq = ++state.planSeq;
  if (!state.plan) renderSkeleton();
  try {
    const plan = await api("/api/plan", { method: "POST", body: JSON.stringify({
      origin: { lat: state.origin.lat, lon: state.origin.lon },
      destination: { lat: state.dest.lat, lon: state.dest.lon },
      depart_at: departIso(),
      trip_name: `${state.origin.name} → ${state.dest.name}`.slice(0, 160),
    }) });
    if (seq !== state.planSeq) return; // a newer request superseded this one
    if (plan.status === "preparing") return showPreparing(plan, seq, fit);
    const keepLabel = state.plan?.routes[state.selected]?.label;
    state.plan = plan;
    const again = plan.routes.findIndex((r) => r.label === keepLabel);
    state.selected = fit || again < 0 ? 0 : again;
    applyTheme();
    drawRoutes(fit);
    renderRoutes();
    writeHash();
    updateTimeLabel();
    loadExplanation();
  } catch (err) {
    if (seq !== state.planSeq) return;
    state.plan = null; drawRoutes(false);
    $("#view").innerHTML = `<div class="error">${icon("error")}<span>${esc(err.message)}</span></div>`;
  }
}

function drawRoutes(fit) {
  if (!map || !map.getSource("pieces")) { state.pendingFit = state.pendingFit || fit; return; }
  state.pendingFit = false;
  const plan = state.plan;
  const routes = plan ? plan.routes : [];
  const line = (coords, props) => ({ type: "Feature", properties: props, geometry: { type: "LineString", coordinates: coords.map(([a, b]) => [b, a]) } });
  map.getSource("alt-routes").setData({ type: "FeatureCollection",
    features: routes.map((r, i) => line(r.geometry, { idx: i })).filter((_, i) => i !== state.selected) });
  const sel = routes[state.selected];
  map.getSource("pieces").setData({ type: "FeatureCollection",
    features: sel ? sel.pieces.map((p) => line(p.coords, { score: p.score, light: p.light })) : [] });
  map.getSource("weak").setData({ type: "FeatureCollection", features: [] });
  map.setPaintProperty("lamps", "circle-opacity", state.dark ? 0.85 : 0);
  map.setPaintProperty("lit-glow", "line-opacity", state.dark ? 0.55 : 0);

  drawLabels();
  if (sel) drawStops(sel);
  if (fit && routes.length) fitTo(routes.flatMap((r) => r.geometry));
}

/* Route time bubbles, placed like Google Maps: on a stretch of the route away from the other
   routes, then slid along the route until it doesn't overlap a bubble already placed on screen. */
function labelCandidates(route, others) {
  const d2 = (a, b) => { const dy = a[0] - b[0], dx = (a[1] - b[1]) * Math.cos(a[0] * Math.PI / 180); return dx * dx + dy * dy; };
  const g = route.geometry, cum = [0];
  for (let k = 1; k < g.length; k++) cum.push(cum[k - 1] + Math.sqrt(d2(g[k - 1], g[k])));
  const total = cum[cum.length - 1] || 1;
  const otherPts = others.flatMap((o) => o.geometry.filter((_, k) => k % 2 === 0));
  const out = [];
  for (let k = 0; k < g.length; k++) {
    const f = cum[k] / total;
    if (f < 0.12 || f > 0.88) continue;
    const clearance = otherPts.length ? Math.min(...otherPts.map((p) => d2(g[k], p))) : 1;
    out.push({ pt: g[k], score: Math.sqrt(clearance) * (1.2 - Math.abs(f - 0.5)) });
  }
  out.sort((a, b) => b.score - a.score);
  return out.length ? out : [{ pt: g[Math.floor(g.length / 2)], score: 0 }];
}

function drawLabels() {
  clearMarkers("labels");
  const routes = state.plan?.routes || [];
  if (!routes.length) return;
  const W = 112, H = 30, placed = [];
  const order = [state.selected, ...routes.map((_, i) => i).filter((i) => i !== state.selected)];
  for (const i of order) {
    const r = routes[i];
    const cands = labelCandidates(r, routes.filter((_, j) => j !== i));
    let pick = cands[0].pt;
    for (const c of cands) {
      const px = map.project([c.pt[1], c.pt[0]]);
      const box = { x: px.x - W / 2, y: px.y - H - 8 };
      if (placed.every((b) => Math.abs(b.x - box.x) > W + 4 || Math.abs(b.y - box.y) > H + 4)) {
        pick = c.pt; placed.push(box); break;
      }
    }
    const el = document.createElement("button");
    el.className = `route-label${i === state.selected ? " selected" : ""}`;
    el.innerHTML = `<span class="dot" style="background:${bandColor(r.score)}"></span>${fmtDur(r.duration_min)}${r.score != null ? ` · ${r.score}` : ""}`;
    el.setAttribute("aria-label", `${r.label}: ${fmtDur(r.duration_min)}, score ${r.score ?? "limited data"}`);
    el.addEventListener("click", (e) => { e.stopPropagation(); selectRoute(i); });
    const m = new maplibregl.Marker({ element: el, anchor: "bottom", offset: [0, -8] }).setLngLat([pick[1], pick[0]]).addTo(map);
    m.getElement().style.zIndex = i === state.selected ? "4" : "3"; // above stop icons
    state.markers.labels.push(m);
  }
}

function drawStops(route) {
  clearMarkers("pois");
  route.stops.forEach((s) => state.markers.pois.push(poiMarker(s, () => flyToPoint(s.lat, s.lon, s.name || s.label))));
}

function fitTo(latlons) {
  const b = new maplibregl.LngLatBounds();
  latlons.forEach(([a, c]) => b.extend([c, a]));
  const phone = isPhone();
  map.fitBounds(b, { padding: phone
    ? { top: 70, bottom: (parseInt(getComputedStyle(document.documentElement).getPropertyValue("--sheet-h")) || 0) + 30, left: 24, right: 76 }
    : { top: 80, bottom: 70, left: 408 + 60, right: 140 }, maxZoom: 16.5, duration: 700 });
}
function flyToPoint(lat, lon, title) {
  map.flyTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), 16), duration: 600 });
  if (title) new maplibregl.Popup({ closeButton: false, offset: 18 }).setLngLat([lon, lat])
    .setHTML(`<div class="popup-title">${esc(title)}</div>`).addTo(map);
}

function selectRoute(i) {
  state.selected = Number(i);
  drawRoutes(false);
  renderRoutes();
  const r = state.plan.routes[state.selected];
  fitTo(r.geometry);
}

/* ---------- rendering: routes ---------- */
function ring(score) {
  const c = 2 * Math.PI * 22, frac = score == null ? 0 : score / 100;
  return `<div class="ring" aria-hidden="true"><svg viewBox="0 0 52 52">
    <circle class="track" cx="26" cy="26" r="22" fill="none" stroke-width="5"/>
    <circle cx="26" cy="26" r="22" fill="none" stroke="${bandColor(score)}" stroke-width="5" stroke-linecap="round"
      stroke-dasharray="${(c * frac).toFixed(1)} ${c.toFixed(1)}"/></svg><b>${score ?? "–"}</b></div>`;
}

function renderSkeleton() {
  $("#view").innerHTML = [1, 2, 3].map(() => `<div class="skeleton"><i style="width:52px;height:52px;border-radius:50%"></i>
    <div style="flex:1"><i style="width:40%;height:18px"></i><i style="width:70%;height:12px;margin-top:8px"></i></div></div>`).join("");
}

function renderRoutes() {
  const plan = state.plan;
  if (!plan) return;
  const html = [explanationCard()];
  plan.routes.forEach((r, i) => {
    const sel = i === state.selected;
    const reasons = r.reasons.filter((x) => !x.startsWith("Street-light data is limited")).slice(0, 2);
    const weak = r.weak_stretches.length;
    html.push(`<button class="route-card${sel ? " selected" : ""}" data-i="${i}" aria-pressed="${sel}">
      <div class="rc-top">${ring(r.score)}
        <div class="rc-main">
          <div class="rc-line1"><span class="rc-dur">${fmtDur(r.duration_min)}</span><span class="rc-dist">${fmtDist(r.distance_m)}</span></div>
          <div class="rc-line2">
            <span class="badge band-${bandKey(r.score)}">${esc(r.band)}</span>
            <span class="badge label-badge${i === 0 && r.label === "Most support" ? " best" : ""}${r.source === "google" ? " google" : ""}">${i === 0 && r.label === "Most support" ? icon("verified") : ""}${r.source === "google" ? icon("map") : ""}${esc(r.label)}</span>
            ${r.also_google ? `<span class="badge label-badge google">${icon("map")}Also Google's route</span>` : ""}
          </div>
        </div>
      </div>
      <ul class="rc-reasons">${r.plan_b ? `<li>${icon("alt_route")}<span>${esc(backupSummary(r.plan_b))}</span></li>` : ""}${reasons.map((x) => `<li>${icon("check_circle")}<span>${esc(x)}</span></li>`).join("")}
        ${weak ? `<li class="warn">${icon("warning")}<span>${weak} stretch${weak > 1 ? "es" : ""} with less support (${fmtDist(r.weak_stretches.reduce((a, w) => a + w.length_m, 0))})</span></li>` : ""}
      </ul>
    </button>${sel ? renderDetail(r) : ""}`);
  });
  $("#view").innerHTML = html.join("");
  $("#view").querySelectorAll(".route-card").forEach((b) => b.addEventListener("click", () => selectRoute(b.dataset.i)));
  $("#view").querySelectorAll("[data-weak]").forEach((b) => b.addEventListener("click", () => showWeak(Number(b.dataset.weak))));
  $("#view").querySelectorAll("[data-sv]").forEach((b) => b.addEventListener("click", () => openStreetView(Number(b.dataset.sv))));
  $("#view").querySelectorAll("[data-stop]").forEach((b) => b.addEventListener("click", () => {
    const s = plan.routes[state.selected].stops[Number(b.dataset.stop)];
    flyToPoint(s.lat, s.lon, s.name || s.label);
  }));
  bindBackup();
  $("#explainBtn")?.addEventListener("click", openExplain);
  $("#view").querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => {
    state.lang = b.dataset.lang; loadExplanation();
  }));
  $("#copyLinkBtn")?.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(location.href); toast("Route link copied"); } catch { toast("Couldn’t copy. Use the address bar link."); }
  });
}

/* ---------- AI explanation (Gemini when configured, else a template; always fact-checked) ---------- */
function explainKey() {
  return JSON.stringify([state.origin?.lat, state.origin?.lon, state.dest?.lat, state.dest?.lon, departIso()]);
}

async function loadExplanation() {
  const key = explainKey(), lang = state.lang;
  const cur = state.explanation;
  if (cur && cur.key === key && cur.lang === lang && cur.data) { renderExplanation(); return; }
  const seq = ++state.explainSeq;
  state.explanation = { key, lang, data: null };
  renderExplanation();
  try {
    const data = await api("/api/explain", { method: "POST", body: JSON.stringify({
      origin: { lat: state.origin.lat, lon: state.origin.lon },
      destination: { lat: state.dest.lat, lon: state.dest.lon },
      depart_at: departIso(), lang,
    }) });
    if (seq !== state.explainSeq) return;
    state.explanation = { key, lang, data };
  } catch (err) {
    if (seq !== state.explainSeq) return;
    state.explanation = { key, lang, data: null, error: err.message };
  }
  renderExplanation();
}

function explanationCard() {
  const e = state.explanation;
  const source = e?.data ? (e.data.provider === "gemini" ? "Explained by Gemini"
    : e.data.provider === "ollama" ? "Explained by a local model" : "Summary from route data") : "Explaining…";
  const body = e?.error ? `<p class="ai-text muted">Couldn’t load the explanation: ${esc(e.error)}</p>`
    : e?.data ? `<p class="ai-text" lang="${esc(e.data.lang)}">${esc(e.data.text)}</p>`
    : `<div class="ai-loading"><i></i><i></i><i style="width:60%"></i></div>`;
  const langs = Object.entries(state.meta.languages).map(([k, l]) =>
    `<button data-lang="${k}" aria-pressed="${state.lang === k}" aria-label="${esc(l.name)}">${esc(l.short)}</button>`).join("");
  return `<section class="ai-card" id="aiCard" aria-live="polite">
    <div class="ai-head">${icon("auto_awesome")}<span>${source}</span><div class="lang-toggle">${langs}</div></div>
    ${body}
    ${e?.data ? `<div class="ai-foot">${icon("fact_check")}Checked against this route’s data: no invented places or numbers</div>` : ""}
  </section>`;
}

function renderExplanation() {
  const card = $("#aiCard");
  if (!card) return;
  card.outerHTML = explanationCard();
  $("#aiCard").querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => {
    state.lang = b.dataset.lang; loadExplanation();
  }));
}

function renderDetail(r) {
  const bars = r.breakdown.map((b) => {
    const val = b.value == null ? null : b.value;
    const conf = b.confidence === 0 ? "no data" : b.confidence < 0.35 ? "low confidence" : b.confidence < 0.65 ? "medium confidence" : "high confidence";
    return `<div class="bar-row"><div>${esc(b.label[0].toUpperCase() + b.label.slice(1))}<span class="conf">${conf} · weight ${Math.round(b.base_weight * 100)}%</span></div>
      <div class="bar"><i style="width:${val ?? 0}%;background:${val == null ? "transparent" : bandColor(val)}"></i></div>
      <div class="bar-val">${val ?? "–"}</div></div>`;
  }).join("");
  const weak = r.weak_stretches.map((w, i) => `<li><button data-weak="${i}">
      <span class="ico" style="background:${COLORS.low}">${icon("warning")}</span>
      <div><b>${fmtDist(w.length_m)} stretch · score ${w.score}</b><small>Mainly: ${esc(w.main_factors.join(", "))}</small></div></button>
      ${googleOn("streetview") ? `<button class="btn sv-btn" data-sv="${i}" aria-label="Street View of this stretch">${icon("streetview")}Street View</button>` : ""}</li>`).join("");
  const stops = r.stops.map((s, i) => {
    const st = KIND_STYLE[s.kind] || KIND_STYLE.open_places;
    const status = s.open ? (s.hours_assumed ? "Likely open (typical hours)" : "Open now") : "Probably closed now";
    return `<li><button data-stop="${i}"><span class="ico" style="background:${st.color}">${icon(st.icon)}</span>
      <div><b>${esc(s.name || st.label)}</b><small>${esc(st.label)} · ${fmtDist(s.along_route_m)} along route, ${fmtDist(s.distance_from_route_m)} off · ${status}</small></div></button></li>`;
  }).join("");
  return `<div class="detail">
    ${renderBackup(r)}
    <h3>Why this score</h3><div class="bars">${bars}</div>
    ${weak ? `<h3>Stretches with less support</h3><ul class="list">${weak}</ul>` : ""}
    ${stops ? `<h3>Stops along this route</h3><ul class="list">${stops}</ul>` : ""}
    <div class="actions">
      <a class="btn primary" href="${googleMapsUrl(r)}" target="_blank" rel="noopener">${icon("navigation")}Start in Google Maps</a>
      <button class="btn" id="copyLinkBtn">${icon("link")}Copy route link</button>
      <button class="btn" id="explainBtn">${icon("help")}How is this calculated?</button>
    </div>
    <details class="notes"><summary>About this data</summary><ul>
      ${state.plan.data_notes.map((n) => `<li>${esc(n)}</li>`).join("")}
      ${r.reasons.filter((x) => x.startsWith("Street-light data is limited")).map((n) => `<li>${esc(n)}</li>`).join("")}
      <li>Map data ${esc(state.plan.attribution)} · basemap ${googleOn("map_tiles") ? "© Google" : "© CARTO"}. Weights v${esc(state.plan.weights_version)}.</li></ul></details>
  </div>`;
}

function bearing([lat1, lon1], [lat2, lon2]) {
  const r = Math.PI / 180, y = Math.sin((lon2 - lon1) * r) * Math.cos(lat2 * r);
  const x = Math.cos(lat1 * r) * Math.sin(lat2 * r) - Math.sin(lat1 * r) * Math.cos(lat2 * r) * Math.cos((lon2 - lon1) * r);
  return (Math.atan2(y, x) / r + 360) % 360;
}

async function openStreetView(i) {
  const w = state.plan.routes[state.selected].weak_stretches[i];
  const mid = [(w.start[0] + w.end[0]) / 2, (w.start[1] + w.end[1]) / 2];
  showWeak(i);
  const dlg = $("#streetViewDialog"), img = $("#streetViewImg"), note = $("#streetViewNote");
  img.hidden = true; note.textContent = "Checking for Street View imagery…"; dlg.showModal();
  try {
    const { available } = await api(`/api/google/streetview/available?lat=${mid[0]}&lon=${mid[1]}`);
    if (!available) { note.textContent = "Google has no Street View imagery for this stretch."; return; }
    img.src = `/api/google/streetview?lat=${mid[0]}&lon=${mid[1]}&heading=${bearing(w.start, w.end).toFixed(0)}`;
    img.alt = `Street View of the ${fmtDist(w.length_m)} stretch scoring ${w.score}`;
    img.hidden = false;
    note.textContent = `${fmtDist(w.length_m)} stretch · score ${w.score} · mainly ${w.main_factors.join(", ")}. Imagery is usually taken in daytime and may be years old.`;
  } catch (err) { note.textContent = err.message; }
}

function showWeak(i) {
  const w = state.plan.routes[state.selected].weak_stretches[i];
  const pieces = state.plan.routes[state.selected].pieces;
  // Highlight the pieces between the stretch's start and end.
  const s = pieces.findIndex((p) => p.coords[0][0] === w.start[0] && p.coords[0][1] === w.start[1]);
  const e = pieces.findIndex((p) => { const c = p.coords[p.coords.length - 1]; return c[0] === w.end[0] && c[1] === w.end[1]; });
  const coords = s >= 0 && e >= s ? pieces.slice(s, e + 1).flatMap((p) => p.coords) : [w.start, w.end];
  map.getSource("weak").setData({ type: "FeatureCollection", features: [{ type: "Feature", properties: {},
    geometry: { type: "LineString", coordinates: coords.map(([a, b]) => [b, a]) } }] });
  fitTo(coords);
}

function googleMapsUrl(r) {
  const g = r.geometry, pick = (f) => g[Math.floor((g.length - 1) * f)];
  const wps = [0.25, 0.5, 0.75].map(pick).map(([a, b]) => `${a.toFixed(6)},${b.toFixed(6)}`).join("|");
  const p = new URLSearchParams({ api: "1", origin: `${g[0][0]},${g[0][1]}`, destination: `${g[g.length - 1][0]},${g[g.length - 1][1]}`,
    travelmode: "walking", waypoints: wps });
  return `https://www.google.com/maps/dir/?${p}`;
}

/* ---------- explain dialog ---------- */
function openExplain() {
  const m = state.meta, f = state.plan?.night_fraction ?? 0;
  const labels = { lighting: "Street lighting", transit: "Transit nearby", safe_spaces: "Open places nearby", incidents: "Historical incidents", reports: "Recent community reports" };
  const how = {
    lighting: "OpenStreetMap lit tags (high confidence), mapped street lamps, NASA VIIRS night lights where loaded, else an estimate from road type (low confidence).",
    transit: "Distance to the nearest bus or metro stop open at this time: ≤150 m = 100, ≥800 m = 0.",
    safe_spaces: "Police, hospitals, pharmacies, shops, cafés and fuel stations open at this time within 150 m. Police and hospitals count 1.5×.",
    incidents: "NCRB district-level crimes against women in public spaces (Delhi only). One value per district, low confidence.",
    reports: "SafeHer community reports within 50 m, fading by half every 7 days; unverified reports count half.",
  };
  const rows = Object.keys(labels).map((k) => {
    const w = f * m.night_weights[k] + (1 - f) * m.day_weights[k];
    return `<tr><td>${icon(SIGNAL_ICONS[k])} ${labels[k]}</td><td><b>${Math.round(w * 100)}%</b><br><span class="muted small">night ${Math.round(m.night_weights[k] * 100)} · day ${Math.round(m.day_weights[k] * 100)}</span></td><td>${how[k]}</td></tr>`;
  }).join("");
  $("#explainBody").innerHTML = `<div class="explain">
    <p>Each route is split into ~50 m stretches. Every stretch gets five signal scores (0–100). Signals with less data count for less: missing data is never treated as a bad score.</p>
    <table><thead><tr><th>Signal</th><th>Weight now</th><th>How it’s measured</th></tr></thead><tbody>${rows}</tbody></table>
    <h3>Route score</h3>
    <code>score = 0.8 × average of all stretches + 0.2 × the weakest 10% of the route</code>
    <p>The second term stops one dark stretch from being averaged away. Lighting weighs more after sunset; weights blend over 30 minutes around sunrise and sunset.</p>
    <h3>Bands</h3><p>75–100 <b>More support</b> · 50–74 <b>Mixed</b> · 0–49 <b>Less support</b>. We never call a route “safe”.</p>
    <h3>Limits</h3><ul>
      <li>Lighting is estimated for much of these areas; estimated stretches are marked low confidence.</li>
      <li>Opening hours are assumed from typical hours when not mapped.</li>
      <li>Incident data is coarse and the same for every route in an area.</li></ul>
    <p class="small muted">Weights version ${esc(m.weights_version)}. Full method: docs/DESIGN_SPEC.md §7–8.</p></div>`;
  $("#explainDialog").showModal();
}

/* ---------- nearby (category chips) ---------- */
function setChip(kind) {
  state.nearbyKind = kind;
  document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.kind === kind)));
}

async function showNearby(kind) {
  if (state.nearbyKind === kind && state.mode === "nearby") { backToRoutes(); return; }
  setChip(kind); state.mode = "nearby"; hideBanner();
  const from = state.origin || { name: "map centre", lat: map.getCenter().lat, lon: map.getCenter().lng };
  const st = KIND_STYLE[kind];
  renderSkeleton();
  try {
    const res = await api(`/api/nearby?lat=${from.lat}&lon=${from.lon}&kind=${kind}&depart_at=${encodeURIComponent(departIso())}`);
    if (state.mode !== "nearby" || state.nearbyKind !== kind) return;
    state.nearby = res.results;
    clearMarkers("pois");
    const items = res.results.map((p, i) => {
      const ps = KIND_STYLE[p.kind] || st;
      state.markers.pois.push(poiMarker(p, () => flyToPoint(p.lat, p.lon, p.name)));
      const status = p.hours_source === "google"
        ? (p.open === true ? "Open now · Google" : p.open === false ? "Closed now · Google" : "Hours unknown · Google")
        : p.open ? (p.hours_assumed ? "Likely open (typical hours)" : "Open now") : "Probably closed now";
      return `<li><div><span class="ico" style="background:${ps.color}">${icon(ps.icon)}</span>
        <div style="flex:1;min-width:0"><b>${esc(p.name)}</b><small>${esc(ps.label)} · ${fmtDist(p.distance_m)} · ${status}${p.lamps_nearby ? " · street lamps nearby" : ""}</small></div>
        ${p.phone ? `<a class="icon-btn" href="tel:${esc(p.phone.split(";")[0])}" aria-label="Call">${icon("call")}</a>` : ""}
        <button class="icon-btn" data-go="${i}" aria-label="Directions to ${esc(p.name)}">${icon("directions")}</button></div></li>`;
    }).join("");
    $("#view").innerHTML = `<div class="view-head"><button class="icon-btn" id="backBtn" aria-label="Back to routes">${icon("arrow_back")}</button>
      <h2>${kind === "open_places" ? "Open & staffed places" : st.label + "s"} near ${esc(from.name)}</h2></div>
      ${items ? `<ul class="list" style="padding:4px 16px 12px">${items}</ul>` : `<div class="empty">Nothing found within ${fmtDist(state.meta.nearby_radius_m)}.</div>`}`;
    $("#backBtn").addEventListener("click", backToRoutes);
    $("#view").querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => {
      const p = state.nearby[Number(b.dataset.go)];
      setPoint("dest", { name: p.name, lat: p.lat, lon: p.lon });
      planTrip(true);
    }));
    if (res.results.length) fitTo([[from.lat, from.lon], ...res.results.map((p) => [p.lat, p.lon])]);
  } catch (err) {
    $("#view").innerHTML = `<div class="error">${icon("error")}<span>${esc(err.message)}</span></div>`;
  }
}

function backToRoutes() {
  setChip(null); state.mode = "routes"; clearMarkers("pois"); hideBanner();
  if (state.markers.temp) { state.markers.temp.remove(); state.markers.temp = null; }
  if (state.plan) { drawRoutes(false); renderRoutes(); } else planTrip(true);
}

/* ---------- community reports ---------- */
function startReport(point = null) {
  setChip(null); clearMarkers("pois");
  state.mode = "report-pick"; state.reportPoint = point;
  $("#view").innerHTML = `<div class="view-head"><button class="icon-btn" id="backBtn" aria-label="Cancel report">${icon("close")}</button><h2>Report something on the street</h2></div>
    <div class="cats">${Object.entries(REPORT_CATS).map(([k, c]) => `<button class="cat${k === "positive" ? " positive" : ""}" data-cat="${k}">${icon(c.icon)}${c.label}</button>`).join("")}</div>
    <div class="privacy">${icon("lock")}<span>Anonymous. The location is snapped to the nearest street, and reports fade after 30 days. Reports affect route scores straight away but count half until others confirm them.</span></div>`;
  $("#backBtn").addEventListener("click", backToRoutes);
  $("#view").querySelectorAll("[data-cat]").forEach((b) => b.addEventListener("click", () => {
    state.reportCat = b.dataset.cat;
    if (state.reportPoint) submitReport(state.reportPoint);
    else { state.mode = "report-place"; showBanner(`Tap the map where it is: ${REPORT_CATS[state.reportCat].label.toLowerCase()}`); }
  }));
  if (isPhone()) $("#panel").classList.remove("collapsed");
}

async function submitReport(pt) {
  hideBanner();
  try {
    await api("/api/reports", { method: "POST", body: JSON.stringify({ lat: pt.lat, lon: pt.lon, category: state.reportCat }) });
    toast("Thanks. Your report helps others. Routes updated.");
    state.reportCat = null; state.reportPoint = null;
    await loadReports();
    backToRoutes(); planTrip(false);
  } catch (err) { toast(err.message); backToRoutes(); }
}

async function loadReports() {
  clearMarkers("reports");
  if (!state.area) return;
  try {
    const rows = await api(`/api/reports?area=${state.area.id}`);
    rows.forEach((r) => {
      const c = REPORT_CATS[r.category];
      const el = document.createElement("div");
      el.className = `report-pin${r.category === "positive" ? " positive" : ""}`;
      el.innerHTML = icon(c.icon); el.title = `${c.label} (reported ${new Date(r.created_at).toLocaleDateString()})`;
      state.markers.reports.push(new maplibregl.Marker({ element: el }).setLngLat([r.lon, r.lat]).addTo(map));
    });
  } catch { /* optional */ }
}

function showBanner(text) {
  const b = $("#modeBanner");
  b.innerHTML = `${icon("touch_app")}<span>${esc(text)}</span><button id="bannerCancel">Cancel</button>`;
  b.hidden = false; $("#bannerCancel").addEventListener("click", backToRoutes);
}
function hideBanner() { $("#modeBanner").hidden = true; }

/* ---------- map click ---------- */
let clickPopup;
function onMapClick(e) {
  const alt = map.getLayer("alt-line") ? map.queryRenderedFeatures(e.point, { layers: ["alt-line", "alt-casing"] }) : [];
  if (alt.length && state.mode === "routes") return selectRoute(alt[0].properties.idx);
  const pt = { lat: e.lngLat.lat, lon: e.lngLat.lng };
  if (state.mode === "report-place") return submitReport(pt);
  clickPopup?.remove();
  const el = document.createElement("div");
  el.className = "popup-actions";
  el.innerHTML = `<button data-a="origin">${icon("trip_origin")}Directions from here</button>
    <button data-a="dest">${icon("location_on")}Directions to here</button>
    <button data-a="report">${icon("add_location_alt")}Report something here</button>`;
  el.addEventListener("click", (ev) => {
    const a = ev.target.closest("button")?.dataset.a; if (!a) return;
    clickPopup.remove();
    if (a === "report") return startReport(pt);
    setPoint(a, { name: "Dropped pin", ...pt });
    planTrip(true);
  });
  clickPopup = new maplibregl.Popup({ closeButton: false, offset: 6 }).setLngLat(e.lngLat).setDOMContent(el).addTo(map);
}

/* ---------- area & time ---------- */
/* The area's configured default trip, else its first two landmarks, else nothing. */
function defaultTrip(area) {
  const byName = (n) => area.landmarks.find((l) => l.name === n);
  if (area.default_trip) return area.default_trip.map(byName);
  return [area.landmarks[0] || null, area.landmarks[1] || null];
}

function renderAreaSwitch() {
  $("#areaSwitch").innerHTML = state.areas.map((a) =>
    `<button role="tab" data-id="${a.id}" aria-selected="${a.id === state.area.id}">${esc(a.city === "Bengaluru" ? "Bengaluru" : a.city)}</button>`).join("");
  $("#areaSwitch").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => switchArea(b.dataset.id)));
}

/* Make a configured area current (city switcher, overlays) without touching the trip. */
function useArea(id) {
  state.area = state.areas.find((a) => a.id === id) || state.area;
  renderAreaSwitch(); loadOverlays(); loadReports();
}

function switchArea(id, keepPoints = false, plan = true) {
  state.area = state.areas.find((a) => a.id === id) || state.areas[0];
  renderAreaSwitch();
  if (!keepPoints) {
    const [o, d] = defaultTrip(state.area);
    setPoint("origin", o); setPoint("dest", d);
  }
  state.plan = null;
  map.jumpTo({ center: [state.area.center[1], state.area.center[0]], zoom: 14 });
  loadOverlays(); loadReports();
  if (plan) planTrip(true);
}

function updateTimeLabel() {
  const { min } = nowParts();
  const m = state.isNow ? min : state.departMin;
  $("#timeSlider").value = String(Math.min(1425, Math.round(m / 15) * 15));
  $("#timeLabel").textContent = state.isNow ? `Leave now · ${fmtClock(min)}` : `Leave at ${fmtClock(m)}`;
  const night = state.plan ? state.plan.night_fraction >= 0.5 : (m < 360 || m >= 1110);
  $("#dayNightIcon").textContent = night ? "dark_mode" : "light_mode";
  $("#nowBtn").hidden = state.isNow;
}

/* ---------- URL state (shareable route links) ---------- */
function writeHash() {
  const f = (p) => `${p.lat.toFixed(5)},${p.lon.toFixed(5)},${encodeURIComponent(p.name)}`;
  const h = new URLSearchParams({ area: state.area.id, o: f(state.origin), d: f(state.dest) });
  if (!state.isNow) h.set("t", String(state.departMin));
  history.replaceState(null, "", `#${h}`);
}
function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  const parse = (v) => { if (!v) return null; const [lat, lon, ...n] = v.split(","); return { lat: +lat, lon: +lon, name: decodeURIComponent(n.join(",")) || "Dropped pin" }; };
  return { area: h.get("area"), o: parse(h.get("o")), d: parse(h.get("d")), t: h.has("t") ? Number(h.get("t")) : null };
}

/* ---------- sheet (phones) ---------- */
function setupSheet() {
  const panel = $("#panel");
  const update = () => document.documentElement.style.setProperty("--sheet-h", isPhone() ? `${panel.getBoundingClientRect().height}px` : "0px");
  new ResizeObserver(update).observe(panel);
  addEventListener("resize", update);
  $("#sheetHandle").addEventListener("click", () => {
    if (panel.classList.contains("expanded")) { panel.classList.remove("expanded"); panel.classList.add("collapsed"); }
    else if (panel.classList.contains("collapsed")) panel.classList.remove("collapsed");
    else panel.classList.add("expanded");
  });
}

/* ---------- boot ---------- */
async function main() {
  [state.areas, state.meta] = await Promise.all([api("/api/areas"), api("/api/meta")]);
  $("#googleBrand").hidden = !googleOn("map_tiles");
  $("#helplines").innerHTML = state.meta.helplines.filter((h) => h.number !== "112")
    .map((h) => `<a href="tel:${h.number}"><b>${h.number}</b><span>${esc(h.label)}</span></a>`).join("");

  const hash = readHash();
  state.area = state.areas.find((a) => a.id === hash.area) || state.areas.find((a) => a.id === state.meta.default_area) || state.areas[0];
  if (hash.t != null) { state.isNow = false; state.departMin = hash.t; }
  else if (state.meta.default_departure) { // config/app.json: e.g. "21:30" for night demos
    const [h, m] = state.meta.default_departure.split(":").map(Number);
    state.isNow = false; state.departMin = h * 60 + m;
  } else { state.isNow = true; }

  renderAreaSwitch();
  const preset = hash.o && hash.d ? [hash.o, hash.d] : defaultTrip(state.area);
  $("#originInput").value = preset[0]?.name || ""; $("#destInput").value = preset[1]?.name || "";
  updateTimeLabel(); renderSkeleton();
  await initMap(state.area.center);
  setupSheet();
  setupSearch("#originInput", "#originSuggest", "origin");
  setupSearch("#destInput", "#destSuggest", "dest");
  renderAreaSwitch();

  if (hash.o && hash.d) { setPoint("origin", hash.o); setPoint("dest", hash.d); switchArea(state.area.id, true); }
  else switchArea(state.area.id);
  updateTimeLabel();

  const replan = debounce(() => planTrip(false), 250);
  $("#timeSlider").addEventListener("input", (e) => { state.isNow = false; state.departMin = Number(e.target.value); updateTimeLabel(); replan(); });
  $("#nowBtn").addEventListener("click", () => { state.isNow = true; updateTimeLabel(); planTrip(false); });
  $("#swapBtn").addEventListener("click", () => { const o = state.origin; setPoint("origin", state.dest); setPoint("dest", o); planTrip(false); });
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => showNearby(c.dataset.kind)));
  $("#reportBtn").addEventListener("click", () => startReport());
  $("#sosBtn").addEventListener("click", () => $("#sosDialog").showModal());
  $("#themeBtn").addEventListener("click", () => {
    state.themePref = { auto: "dark", dark: "light", light: "auto" }[state.themePref];
    applyTheme(); toast(`Map theme: ${state.themePref === "auto" ? "auto (dark after sunset)" : state.themePref}`, 1800);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.mode !== "routes") backToRoutes(); });
  setInterval(() => { if (state.isNow) updateTimeLabel(); }, 30_000);

}

main().catch((err) => {
  document.body.insertAdjacentHTML("beforeend", `<div class="toast">Couldn’t start SafeHer: ${esc(err.message)}. Is the backend running?</div>`);
});
