"use strict";
(() => {
  const token = location.hash.slice(1);
  const status = document.querySelector("#liveStatus");
  const expiry = document.querySelector("#liveExpiry");
  const follow = document.querySelector("#followLocation");
  let marker = null, data = null, ended = false, offline = false, routeDrawn = false;
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) {
    status.textContent = "Invalid sharing link. Ask the sender for a new link.";
    follow.disabled = true; return;
  }
  const map = new maplibregl.Map({
    container: "liveMap", style: "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
    center: [77.21, 28.54], zoom: 13,
  });
  map.addControl(new maplibregl.NavigationControl());
  const clear = () => {
    marker?.remove(); marker = null; data = null;
    if (map.getSource("shared-route")) map.getSource("shared-route").setData({ type: "FeatureCollection", features: [] });
    expiry.textContent = ""; follow.disabled = true;
  };
  const describe = () => {
    if (ended) return;
    if (data && Date.now() / 1000 >= data.expires_at) {
      ended = true; clear(); status.textContent = "Sharing expired. Ask for a new link."; return;
    }
    const point = data?.location;
    if (!point) { status.textContent = offline ? "Connection lost. Retrying…" : "Waiting for the sender’s first GPS update…"; return; }
    const age = Math.max(0, Math.floor(Date.now() / 1000 - point.updated_at));
    status.textContent = `${offline ? "Connection lost · " : ""}${age > 30 ? "STALE · " : ""}Last received ${age}s ago · GPS accuracy ±${Math.round(point.accuracy_m)}m`;
  };
  const draw = () => {
    if (!data || !map.isStyleLoaded()) return;
    if (!routeDrawn && data.route.length > 1) {
      map.addSource("shared-route", { type: "geojson", data: {
        type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: data.route },
      } });
      map.addLayer({ id: "shared-route", type: "line", source: "shared-route",
        paint: { "line-color": "#5B2A86", "line-width": 5 } });
      const bounds = new maplibregl.LngLatBounds();
      data.route.forEach((point) => bounds.extend(point));
      map.fitBounds(bounds, { padding: 40, maxZoom: 16 }); routeDrawn = true;
    }
    if (data.location) {
      const point = [data.location.lon, data.location.lat];
      if (!marker) {
        marker = new maplibregl.Marker({ color: "#5B2A86" }).setLngLat(point).addTo(map);
        map.easeTo({ center: point, zoom: 15 });
      } else marker.setLngLat(point);
      follow.disabled = false;
    }
  };
  const poll = async () => {
    if (ended) return;
    try {
      const res = await fetch(`/api/trips/${token}`, { cache: "no-store", signal: AbortSignal.timeout(10000) });
      if (res.status === 404) {
        ended = true; clear(); status.textContent = "Sharing stopped, expired, or link unavailable."; return;
      }
      if (!res.ok) throw new Error("Unavailable");
      data = await res.json(); offline = false;
      expiry.textContent = `Link expires at ${new Date(data.expires_at * 1000).toLocaleString()}. Anyone with this link can view it.`;
      describe(); draw();
    } catch { offline = true; describe(); }
    finally { if (!ended) setTimeout(poll, 5000); }
  };
  follow.disabled = true;
  follow.addEventListener("click", () => {
    if (data?.location) map.easeTo({ center: [data.location.lon, data.location.lat], zoom: 16 });
  });
  map.on("load", draw);
  setInterval(describe, 1000);
  poll();
})();
