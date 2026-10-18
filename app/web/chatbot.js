/* HerSide: ephemeral chat, explicit one-shot GPS, and text-only rendering of provider data. */
"use strict";
(() => {
  const get = (id) => document.getElementById(id);
  const dialog = get("chatbotDialog"), messages = get("chatbotMessages");
  const input = get("chatbotInput"), status = get("chatbotStatus");
  let session = null, position = null, trip = null, controller = null, generation = 0, locating = false;

  function node(tag, text, parent) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (parent) parent.append(element);
    return element;
  }
  function message(speaker, text) {
    const article = node("article", undefined, messages);
    article.className = "herside-message";
    article.dataset.speaker = speaker;
    node("strong", speaker, article);
    node("p", text, article);
    messages.scrollTop = messages.scrollHeight;
    return article;
  }
  function busy(value) {
    get("chatbotSend").disabled = value;
    get("chatbotTrip").disabled = value;
    dialog.querySelectorAll("[data-chat-prompt]").forEach((button) => { button.disabled = value; });
    status.textContent = value ? "HerSide is checking. Emergency calling is always available above." : "";
    messages.querySelector(".herside-typing")?.remove();
    if (value) {  // three animated dots while the agents work
      const dots = node("div", undefined, messages);
      dots.className = "herside-typing";
      dots.setAttribute("aria-hidden", "true");
      for (let i = 0; i < 3; i++) node("i", undefined, dots);
      messages.scrollTop = messages.scrollHeight;
    }
  }
  function icon(name, parent) {
    const span = node("span", name, parent);
    span.className = "msr";
    span.setAttribute("aria-hidden", "true");
    return span;
  }
  function pill(text, parent, kind = "") {
    const span = node("span", text, parent);
    span.className = `herside-pill ${kind}`.trim();
    return span;
  }
  function action(parent, glyph, label, onClick) {
    const button = node("button", undefined, parent);
    button.type = "button";
    icon(glyph, button);
    button.append(label);
    button.addEventListener("click", onClick);
    return button;
  }
  /* Show a place on the SafeHer map (OpenStreetMap results only: Google results stay Google-linked). */
  function showOnMap(lat, lon, name) {
    if (typeof map === "undefined" || !map || typeof maplibregl === "undefined") return;
    dialog.close();
    if (typeof sheet !== "undefined") sheet.reveal();
    if (state.markers.temp) state.markers.temp.remove();
    state.markers.temp = new maplibregl.Marker({ color: "#C2577A" }).setLngLat([lon, lat]).addTo(map);
    map.flyTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), 16), duration: 900 });
  }
  function showRoute(label) {
    if (typeof state === "undefined" || !state.plan || typeof selectRoute !== "function") return false;
    const i = state.plan.routes.findIndex((r) => r.label === label);
    if (i < 0) return false;
    dialog.close();
    if (typeof sheet !== "undefined") sheet.reveal();
    selectRoute(i);
    return true;
  }
  function modelBadge() {
    const info = typeof state !== "undefined" ? state.meta?.assistant : null;
    const badge = get("chatbotModel");
    if (!info || !badge) return;
    const name = info.provider === "gemini"
      ? info.model.replace(/^gemini-/, "Gemini ").replace(/-/g, " ").replace(/\b(flash|pro|lite)\b/g, (w) => w[0].toUpperCase() + w.slice(1))
      : info.model;
    badge.replaceChildren();
    icon(info.provider === "gemini" ? "bolt" : "computer", badge).style.fontSize = "14px";
    node("b", name, badge);
    badge.append(` · ${info.platform}`);
  }
  function locationLabel() {
    get("chatbotRemoveLocation").hidden = !position;
    get("chatbotLocationStatus").textContent = position
      ? `Attached for your next message only · accuracy ±${Math.round(position.accuracy_m)} m. Not sent yet.`
      : "Location not attached.";
  }
  function attachLocation() {
    if (locating) return;
    if (!navigator.geolocation || !window.isSecureContext) {
      status.textContent = "Location needs HTTPS or localhost. You can type a landmark instead.";
      return;
    }
    locating = true;
    const version = generation;
    get("chatbotLocation").disabled = true;
    status.textContent = "Waiting for location permission. Nothing is sent until you submit.";
    navigator.geolocation.getCurrentPosition((fix) => {
      locating = false;
      get("chatbotLocation").disabled = false;
      if (version !== generation) return;
      position = { lat: fix.coords.latitude, lon: fix.coords.longitude,
        accuracy_m: fix.coords.accuracy, observed_at: new Date(fix.timestamp).toISOString() };
      locationLabel();
      status.textContent = "Location attached. Send your question when ready.";
    }, (error) => {
      locating = false;
      get("chatbotLocation").disabled = false;
      if (version === generation) status.textContent = error.code === 1
        ? "Location permission denied. Type a landmark or coordinates instead."
        : "Could not get your location. Type a landmark or try again.";
    }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
  }
  function externalLink(parent, label, url) {
    const link = node("a", label, parent);
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.referrerPolicy = "no-referrer";
  }
  /* Google Maps grounding: sources right after the answer, each attributed to "Google Maps"
     (exact text, not translated), with the returned title linked to the returned URI. */
  function sources(list, article) {
    const box = node("div", undefined, article);
    box.className = "herside-sources";
    const head = node("div", undefined, box);
    head.className = "herside-sources-head";
    node("span", "Sources", head);
    for (const src of list) {
      if (!/^https:\/\//.test(src.uri || "")) continue;
      const row = node("div", undefined, box);
      row.className = "herside-source";
      const label = node("span", "Google Maps", row);
      label.className = "herside-gmaps";
      label.setAttribute("translate", "no");
      externalLink(row, src.title || "Place", src.uri);
      for (const review of src.reviews || []) {
        if (/^https:\/\//.test(review.uri || "")) externalLink(row, "Review", review.uri);
      }
    }
  }
  function render(data) {
    const article = message("HerSide", data.reply || "No reply was available.");
    if (data.sources?.length) sources(data.sources, article);
    for (const place of data.places || []) {
      const card = node("div", undefined, article);
      card.className = "herside-card";
      const title = node("strong", undefined, card);
      icon(place.source === "google_places" ? "travel_explore" : "place", title);
      node("span", place.name || place.address || "Location", title);
      const pills = node("div", undefined, card);
      pills.className = "herside-pills";
      if (place.open_now != null) pill(place.open_now ? "Expected open" : "Expected closed", pills, place.open_now ? "open" : "closed");
      if (place.distance_m != null) pill(`${place.distance_m} m`, pills);
      if (place.neighbourhood) pill(place.neighbourhood, pills);
      if (place.distance_m != null) node("small", place.distance_basis === "straight_line" ? "Straight-line distance, not a walking estimate" : place.distance_basis || "Distance basis unknown", card);
      if (place.hours_basis) node("small", `Hours: ${place.hours_basis.replaceAll("_", " ")}${place.open_now == null ? " · availability unknown" : ""}`, card);
      node("small", `Source: ${place.source || "not specified"}${place.city ? ` · ${place.city}` : ""}${place.snapshot_at ? ` · data from ${place.snapshot_at.slice(0, 10)}` : ""}`, card);
      for (const attribution of place.attributions || []) node("small", typeof attribution === "string" ? attribution : attribution.provider || attribution.providerUri || "Google Maps", card);
      const lat = place.lat, lon = place.lon;
      if (Number.isFinite(lat) && Math.abs(lat) <= 90 && Number.isFinite(lon) && Math.abs(lon) <= 180) {
        const actions = node("div", undefined, card);
        actions.className = "herside-actions";
        // OpenStreetMap places can be shown on the SafeHer map; Google results stay Google-linked.
        if (place.source !== "google_places" && typeof map !== "undefined") action(actions, "map", "Show on map", () => showOnMap(lat, lon, place.name));
        action(actions, "edit_location", "Use these coordinates in a question", () => {
          input.value = `Use confirmed location ${lat}, ${lon}. `;
          input.focus();
        });
        externalLink(actions, "Open Google Maps ↗", `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${lat},${lon}`)}${place.source === "google_places" && place.id ? `&query_place_id=${encodeURIComponent(place.id)}` : ""}`);
      }
    }
    for (const route of data.routes || []) {
      const card = node("div", undefined, article);
      card.className = "herside-card";
      const title = node("strong", undefined, card);
      const score = node("span", route.score == null ? "–" : String(route.score), title);
      score.className = `herside-score ${route.score == null ? "" : route.score >= 80 ? "high" : route.score >= 60 ? "mid" : "low"}`.trim();
      score.setAttribute("aria-hidden", "true");
      node("span", route.label || "Walking estimate", title);
      node("p", `${route.distance_m ?? route.distanceMeters ?? "Unknown"} m · ${route.duration_min != null ? `${route.duration_min} min` : route.duration || "duration unknown"}`, card);
      node("small", route.score == null ? "No SafeHer support score for this estimate." : `Support score: ${route.score}/100 · ${route.band || ""}`, card);
      if (route.plan_b) node("small", `Plan B: ${Math.round(route.plan_b.within_threshold_share * 100)}% within five minutes of a mapped option.`, card);
      if (route.label && typeof state !== "undefined" && state.plan?.routes?.some((r) => r.label === route.label)) {
        const actions = node("div", undefined, card);
        actions.className = "herside-actions";
        action(actions, "route", "Show on map", () => showRoute(route.label));
      }
    }
    for (const action of data.actions || []) {
      if (action.type === "call" && /^tel:(112|181|1091)$/.test(action.value || "")) {
        const link = node("a", action.label, article);
        link.href = action.value;
      } else if (action.type === "request_location") {
        const button = node("button", "Attach my location once", article);
        button.type = "button";
        button.addEventListener("click", attachLocation);
      }
    }
    if (data.warnings?.length) {
      const list = node("ul", undefined, article);
      data.warnings.forEach((warning) => node("li", warning, list));
    }
    messages.scrollTop = messages.scrollHeight;
  }
  async function send(text, sos = false) {
    text = text.trim();
    if (!text || (!sos && controller)) return;
    if (sos && controller) controller.abort();
    const request = new AbortController();
    controller = request;
    const version = generation;
    message("You", text);
    input.value = "";
    busy(true);
    const payload = { message: text, session_id: session, sos, lang: "en" };
    if (trip && !sos) payload.trip = trip;
    trip = null;
    if (position && Date.now() - Date.parse(position.observed_at) <= 120000) payload.location = position;
    position = null;
    locationLabel();
    const timeout = setTimeout(() => request.abort(), 70000);
    try {
      const response = await fetch("/api/chat", { method: "POST", cache: "no-store", signal: request.signal,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (version !== generation || controller !== request) return;
      if (!response.ok) {
        if (response.status === 404) session = null;
        throw new Error(response.status === 404 ? "This chat session expired. Send your question again to start a new one."
          : "HerSide could not complete that request. Try again. For emergencies in India, call 112.");
      }
      const data = await response.json();
      if (version !== generation || controller !== request) return;
      if (data.session_id) session = data.session_id;
      render(data);
    } catch (error) {
      if (version === generation && controller === request) message("HerSide", error.name === "AbortError"
        ? "The request timed out or was cancelled. Emergency calling is still available above."
        : error.message);
    } finally {
      clearTimeout(timeout);
      if (controller === request) { controller = null; busy(false); }
    }
  }
  get("chatbotBtn").addEventListener("click", () => { modelBadge(); dialog.showModal(); input.focus(); });
  input.addEventListener("keydown", (event) => {  // Enter sends; Shift+Enter adds a line
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      get("chatbotForm").requestSubmit();
    }
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
  });
  dialog.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const focusable = [...dialog.querySelectorAll("button:not(:disabled), a[href], textarea, [tabindex='0']")]
      .filter((element) => !element.hidden && element.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  get("chatbotClose").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    position = null;
    trip = null;
    generation += 1;
    controller?.abort(); controller = null; busy(false); locationLabel();
  });
  get("chatbotLocation").addEventListener("click", attachLocation);
  get("chatbotRemoveLocation").addEventListener("click", () => { position = null; locationLabel(); });
  get("chatbotSOS").addEventListener("click", () => send("I don't feel safe", true));
  get("chatbotForm").addEventListener("submit", (event) => { event.preventDefault(); send(input.value); });
  dialog.querySelectorAll("[data-chat-prompt]").forEach((button) => button.addEventListener("click", () => send(button.dataset.chatPrompt)));
  get("chatbotTrip").addEventListener("click", () => {
    if (typeof state === "undefined" || !state.origin || !state.dest) {
      status.textContent = "Select an origin and destination in Directions first, or describe your trip below.";
      return;
    }
    trip = {
      origin: { lat: state.origin.lat, lon: state.origin.lon },
      destination: { lat: state.dest.lat, lon: state.dest.lon },
      depart_at: `${departIso()}+05:30`,
    };
    input.value = "For this walk, is the extra walking time worth it for better lighting and fewer weak stretches?";
    status.textContent = `Trip attached: ${state.origin.name || "selected origin"} → ${state.dest.name || "selected destination"}, departing ${new Date(trip.depart_at).toLocaleString("en-IN")}. Attached for the next message only; review before sending.`;
    input.focus();
  });
  get("chatbotReset").addEventListener("click", () => {
    generation += 1; controller?.abort(); controller = null;
    session = null; position = null; trip = null; messages.replaceChildren();
    input.value = ""; busy(false); locationLabel(); welcome();
    status.textContent = "Cleared in this browser. Server-side context expires after 30 minutes idle.";
  });
  function welcome() { message("HerSide", "Hi, I’m HerSide. Ask about nearby places or compare a walking trip. Give a landmark or attach your location when needed. For immediate danger in India, use Call 112 above."); }
  welcome();
})();
