"""Google Maps Platform client (optional). Everything here is off unless GOOGLE_MAPS_API_KEY is set.

APIs used (enable them on the key's Cloud project):
  Map Tiles API          2D roadmap tiles (light + dark) and viewport attribution
  Places API (New)       Autocomplete, Place Details, Nearby Search (live "open now")
  Routes API             walking routes with alternatives, scored by SafeHer's own engine
  Street View Static API street-level image of a weak stretch (+ free metadata check)

Terms that shape this module (Google Maps Platform policies):
  - Places and Routes content may only be shown on a Google map. The web app therefore switches
    its basemap to Google tiles whenever Google content is shown ("Google mode").
  - Nothing is stored except place IDs, which may be cached indefinitely. Tiles and images are
    passed through without caching (Cache-Control: no-store).
  - The key never reaches the browser; tiles and images are proxied by the backend.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import httpx

from . import config
from .explain import _env

PLACES = "https://places.googleapis.com/v1"
ROUTES = "https://routes.googleapis.com/directions/v2:computeRoutes"
TILES = "https://tile.googleapis.com"
STREETVIEW = "https://maps.googleapis.com/maps/api/streetview"

_G = config.app()["google"]


class GoogleError(Exception):
    """Any failure talking to Google; callers fall back to open data."""


def api_key() -> str:
    return _env("GOOGLE_MAPS_API_KEY")


def enabled() -> bool:
    return bool(api_key())


def feature(name: str) -> bool:
    return enabled() and bool(_G["features"].get(name, False))


# --- Encoded polyline (Google's algorithm, precision 1e5) -----------------------------------


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    coords, index, lat, lon, factor = [], 0, 0, 0, 10 ** precision
    while index < len(encoded):
        for axis in (0, 1):
            shift = result = 0
            while True:
                if index >= len(encoded):
                    raise GoogleError("truncated polyline")
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lat / factor, lon / factor))
    return coords


# --- Client ---------------------------------------------------------------------------------


@dataclass
class _Session:
    token: str
    expires: float
    copyright: dict = field(default_factory=dict)


class GoogleClient:
    """Thin, testable wrapper. Pass `transport` (httpx.MockTransport) in tests."""

    def __init__(self, key: str | None = None, transport: httpx.BaseTransport | None = None):
        self.key = key if key is not None else api_key()
        self.timeout = float(_G["timeout_s"])
        self._http = httpx.Client(timeout=self.timeout, transport=transport,
                                  headers={"User-Agent": "SafeHer/0.2"})
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    # Generic request with uniform error handling.
    def _call(self, method: str, url: str, *, field_mask: str | None = None, **kw) -> httpx.Response:
        if not self.key:
            raise GoogleError("GOOGLE_MAPS_API_KEY not set")
        headers = {**kw.pop("headers", {}), "X-Goog-Api-Key": self.key}
        if field_mask:
            headers["X-Goog-FieldMask"] = field_mask
        try:
            r = self._http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as e:
            raise GoogleError(f"network error: {type(e).__name__}") from e
        if r.status_code != 200:
            detail = ""
            try:
                detail = r.json().get("error", {}).get("message", "")[:200]
            except ValueError:
                pass
            raise GoogleError(f"HTTP {r.status_code}{': ' + detail if detail else ''}")
        return r

    # ---- Map Tiles API --------------------------------------------------------------------

    def tile_session(self, style: str) -> _Session:
        """Session per style ("light"/"dark"), reused until shortly before it expires."""
        with self._lock:
            s = self._sessions.get(style)
            if s and s.expires - time.time() > 3600:
                return s
            body = {"mapType": "roadmap", "language": _G["language"], "region": _G["region"]}
            if style == "dark":
                body["styles"] = _G["dark_map_styles"]
            data = self._call("POST", f"{TILES}/v1/createSession", params={"key": self.key}, json=body).json()
            if "session" not in data:
                raise GoogleError("createSession returned no session")
            s = _Session(data["session"], float(data.get("expiry", time.time() + 86400)))
            self._sessions[style] = s
            return s

    def tile(self, style: str, z: int, x: int, y: int) -> tuple[bytes, str]:
        s = self.tile_session(style)
        r = self._call("GET", f"{TILES}/v1/2dtiles/{z}/{x}/{y}", params={"session": s.token, "key": self.key})
        return r.content, r.headers.get("content-type", "image/png")

    def viewport_copyright(self, style: str, zoom: int, north: float, south: float, east: float, west: float) -> str:
        s = self.tile_session(style)
        r = self._call("GET", f"{TILES}/tile/v1/viewport", params={
            "session": s.token, "key": self.key, "zoom": zoom, "north": north, "south": south, "east": east,
            "west": west})
        return str(r.json().get("copyright", "")).strip()

    # ---- Places API (New) -----------------------------------------------------------------

    def autocomplete(self, text: str, bbox: tuple[float, float, float, float], session_token: str | None = None
                     ) -> list[dict]:
        """bbox = (south, west, north, east). Returns [{place_id, main, secondary}]."""
        south, west, north, east = bbox
        body = {"input": text, "includedRegionCodes": [_G["region"].lower()], "languageCode": _G["language"],
                "locationRestriction": {"rectangle": {"low": {"latitude": south, "longitude": west},
                                                      "high": {"latitude": north, "longitude": east}}}}
        if session_token:
            body["sessionToken"] = session_token
        data = self._call("POST", f"{PLACES}/places:autocomplete", json=body).json()
        out = []
        for s in data.get("suggestions", []):
            p = s.get("placePrediction")
            if not p or not p.get("placeId"):
                continue  # query predictions have no place
            fmt = p.get("structuredFormat", {})
            out.append({"place_id": p["placeId"],
                        "main": fmt.get("mainText", {}).get("text") or p.get("text", {}).get("text", ""),
                        "secondary": fmt.get("secondaryText", {}).get("text", "")})
        return out

    def place(self, place_id: str, session_token: str | None = None) -> dict:
        params = {"sessionToken": session_token} if session_token else None
        data = self._call("GET", f"{PLACES}/places/{place_id}", params=params,
                          field_mask="id,displayName,formattedAddress,location").json()
        loc = data.get("location") or {}
        if "latitude" not in loc or "longitude" not in loc:
            raise GoogleError("place has no location")
        return {"place_id": data.get("id", place_id), "name": (data.get("displayName") or {}).get("text", ""),
                "address": data.get("formattedAddress", ""), "lat": float(loc["latitude"]),
                "lon": float(loc["longitude"])}

    def nearby(self, lat: float, lon: float, types: list[str], radius_m: float, limit: int) -> list[dict]:
        body = {"includedTypes": types, "maxResultCount": max(1, min(20, limit)), "rankPreference": "DISTANCE",
                "languageCode": _G["language"],
                "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lon},
                                                   "radius": float(min(radius_m, 50000))}}}
        mask = ("places.id,places.displayName,places.location,places.primaryType,places.types,"
                "places.businessStatus,places.currentOpeningHours.openNow,places.nationalPhoneNumber")
        data = self._call("POST", f"{PLACES}/places:searchNearby", json=body, field_mask=mask).json()
        out = []
        for p in data.get("places", []):
            loc = p.get("location") or {}
            if "latitude" not in loc or "longitude" not in loc:
                continue
            if p.get("businessStatus") in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
                continue
            hours = p.get("currentOpeningHours")
            out.append({"place_id": p.get("id"), "name": (p.get("displayName") or {}).get("text", ""),
                        "lat": float(loc["latitude"]), "lon": float(loc["longitude"]),
                        "type": p.get("primaryType") or (p.get("types") or [""])[0],
                        "open": hours.get("openNow") if isinstance(hours, dict) else None,
                        "phone": p.get("nationalPhoneNumber")})
        return out

    # ---- Routes API -----------------------------------------------------------------------

    def walking_routes(self, origin: tuple[float, float], dest: tuple[float, float]) -> list[dict]:
        wp = lambda p: {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}  # noqa: E731
        body = {"origin": wp(origin), "destination": wp(dest), "travelMode": "WALK",
                "computeAlternativeRoutes": True, "languageCode": _G["language"], "units": "METRIC"}
        mask = "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline,routes.routeLabels"
        data = self._call("POST", ROUTES, json=body, field_mask=mask).json()
        out = []
        for r in data.get("routes", []):
            enc = (r.get("polyline") or {}).get("encodedPolyline")
            if not enc:
                continue
            pts = decode_polyline(enc)
            if len(pts) < 2:
                continue
            dur = str(r.get("duration", "0s")).rstrip("s")
            out.append({"points": pts, "distance_m": int(r.get("distanceMeters", 0)),
                        "duration_s": float(dur or 0), "labels": r.get("routeLabels", [])})
        return out

    # ---- Street View Static API -----------------------------------------------------------

    def streetview_available(self, lat: float, lon: float, radius_m: int) -> bool:
        """Metadata requests are free; only show the image button where imagery exists."""
        r = self._call("GET", f"{STREETVIEW}/metadata", params={
            "location": f"{lat},{lon}", "radius": radius_m, "source": "outdoor", "key": self.key})
        return r.json().get("status") == "OK"

    def streetview_image(self, lat: float, lon: float, heading: float, radius_m: int) -> tuple[bytes, str]:
        w, h = _G["streetview_size"]
        r = self._call("GET", STREETVIEW, params={
            "size": f"{w}x{h}", "location": f"{lat},{lon}", "heading": round(heading % 360),
            "fov": 90, "pitch": 0, "radius": radius_m, "source": "outdoor", "return_error_code": "true",
            "key": self.key})
        return r.content, r.headers.get("content-type", "image/jpeg")


_client: GoogleClient | None = None


def client() -> GoogleClient:
    global _client
    if _client is None or _client.key != api_key():
        _client = GoogleClient()
    return _client
