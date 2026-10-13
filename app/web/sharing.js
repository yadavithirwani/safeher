/* Foreground-only sharing. Owner credential never enters the viewer URL. */
"use strict";
(() => {
  const dialog = document.querySelector("#sharingDialog");
  const status = document.querySelector("#sharingStatus");
  const start = document.querySelector("#startSharing");
  const stop = document.querySelector("#stopSharing");
  const link = document.querySelector("#sharingLink");
  const copy = document.querySelector("#copySharing");
  let session = null, watch = null, timer = null, latest = null, busy = false;
  let lastSent = 0, latestAt = 0;
  try { session = JSON.parse(sessionStorage.getItem("safeher-sharing")); } catch { /* Storage unavailable. */ }
  const say = (text) => { status.textContent = text; };
  const save = () => {
    try {
      if (session) sessionStorage.setItem("safeher-sharing", JSON.stringify(session));
      else sessionStorage.removeItem("safeher-sharing");
    } catch { /* Sharing still works without reload recovery. */ }
  };
  const render = () => {
    start.disabled = Boolean(session);
    stop.hidden = !session;
    copy.hidden = !session;
    link.hidden = !session;
    link.value = session ? `${location.origin}/live#${session.viewer_token}` : "";
    document.querySelector("#sharingBtn").textContent = session ? "Sharing · manage" : "Share live location";
  };
  const clearWatch = () => {
    if (watch !== null) navigator.geolocation.clearWatch(watch);
    watch = null;
    clearInterval(timer);
    timer = null;
    latest = null;
  };
  const request = async (path, options = {}) => {
    const res = await fetch(path, { ...options, signal: AbortSignal.timeout(10000) });
    if (!res.ok) {
      const error = new Error(`Request failed (${res.status})`);
      error.status = res.status;
      throw error;
    }
    return res.status === 204 ? null : res.json();
  };
  const send = async () => {
    if (!session || busy) return;
    if (Date.now() / 1000 >= session.expires_at) {
      clearWatch(); session = null; save(); render();
      say("Sharing expired. Start a new session if needed."); return;
    }
    if (!latest || Date.now() - lastSent < 5000) return;
    if (Date.now() - latestAt > 30000) {
      latest = null; say("GPS fix is too old to send. Waiting for a fresh location."); return;
    }
    const current = session, sending = latest;
    busy = true;
    try {
      await request(`/api/trips/${current.viewer_token}/location`, {
        method: "PUT", headers: { "Content-Type": "application/json", Authorization: `Bearer ${current.owner_token}` },
        body: JSON.stringify(sending),
      });
      lastSent = Date.now();
      if (latest === sending) latest = null;
      if (session === current) say(`Location sent at ${new Date().toLocaleTimeString()}. Keep this page open.`);
    } catch (error) {
      if (session === current) {
        if (error.status === 404) {
          clearWatch(); session = null; save(); render();
          say("Sharing stopped or expired.");
        } else say("Location not sent. Retrying—contacts may see an old position.");
      }
    } finally { busy = false; }
  };
  const track = () => {
    clearWatch();
    watch = navigator.geolocation.watchPosition((position) => {
      latestAt = position.timestamp;
      latest = { lat: position.coords.latitude, lon: position.coords.longitude, accuracy_m: position.coords.accuracy };
      send();
    }, (error) => say(error.code === 1
      ? "Location permission denied. Allow location in browser settings, or stop sharing."
      : "GPS unavailable. Contacts may see an old position; trying again."),
    { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 });
    timer = setInterval(send, 5000);
  };
  document.querySelector("#sharingBtn").addEventListener("click", () => dialog.showModal());
  start.addEventListener("click", async () => {
    if (!window.isSecureContext || !navigator.geolocation) {
      say("Location needs HTTPS (or localhost) and a browser with GPS support."); return;
    }
    start.disabled = true; say("Waiting for your location permission…");
    try {
      // Ask permission before creating a server session.
      const point = await new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(resolve, reject,
        { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 }));
      const route = typeof state !== "undefined" && state.plan
        ? state.plan.routes[state.selected].geometry.map(([lat, lon]) => [lon, lat]) : [];
      session = await request("/api/trips", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ route }),
      });
      save(); render(); track();
      latestAt = point.timestamp;
      latest = { lat: point.coords.latitude, lon: point.coords.longitude, accuracy_m: point.coords.accuracy };
      await send();
    } catch (error) {
      say(error.code === 1 ? "Location permission denied. No sharing started." : "Could not start sharing. Check GPS and connection.");
      render();
    }
  });
  stop.addEventListener("click", async () => {
    if (!session) return;
    clearWatch(); stop.disabled = true;
    const current = session;
    try {
      await request(`/api/trips/${current.viewer_token}`, {
        method: "DELETE", headers: { Authorization: `Bearer ${current.owner_token}` },
      });
      session = null; save(); render(); say("Sharing stopped. The link and stored location are deleted.");
    } catch (error) {
      if (error.status === 404) {
        session = null; save(); render(); say("Sharing already stopped or expired.");
      } else say("GPS stopped on this device, but the server could not revoke the link. Retry Stop; the last point remains until expiry.");
    } finally { stop.disabled = false; }
  });
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(link.value); say("Viewer link copied. Share it only with people you trust."); }
    catch { link.focus(); link.select(); say("Copy the selected viewer link manually."); }
  });
  document.addEventListener("visibilitychange", () => {
    if (!session) return;
    if (document.hidden) say("Page in background: updates may pause. Keep the app open for live sharing.");
    else send();
  });
  // Reload recovery does not silently resume GPS: user must explicitly resume.
  if (session && session.expires_at <= Date.now() / 1000) { session = null; save(); }
  if (session) {
    say("A previous sharing link is still active. Stop it, or resume GPS updates.");
    const resume = document.createElement("button");
    resume.textContent = "Resume location updates";
    resume.addEventListener("click", () => {
      if (navigator.geolocation && window.isSecureContext) { track(); resume.remove(); }
      else say("GPS requires HTTPS or localhost.");
    });
    stop.before(resume);
    stop.addEventListener("click", () => resume.remove(), { once: true });
  }
  render();
})();
