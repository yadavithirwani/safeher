"""HTTP API + demo web app.  Run:  uvicorn safeher.api:app --reload   (from backend/)"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
import copy
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import trips
from .chatbot.service import router as chatbot_router
from . import signals as sig
from . import config, google, store, userdb
from .cities import CITIES, Unsupported, city_at, corridor, supported_list
from .config import REPO_ROOT
from .geo import LatLon, haversine_m
from .explain import LANGUAGES, ExplainFailed, explain
from .plan import DISCLAIMER, KIND_LABELS, plan
from .pois import pois_from_osm
from .search import search as place_search
from .scoring import BANDS, DAY_WEIGHTS, NIGHT_WEIGHTS, WEIGHTS_VERSION

WEB_DIR = REPO_ROOT / "app" / "web"

APP = config.app()
GOOGLE = APP["google"]
DEFAULT_CITY = APP["default_city"]
if DEFAULT_CITY not in CITIES:
    raise RuntimeError(f"config/app.json default_city {DEFAULT_CITY!r} is not in config/cities.json")


def g_on(feature: str) -> bool:
    """A Google feature is usable only with its own switch AND the Google basemap: Google's terms
    don't allow Places or Routes content on a non-Google map."""
    return google.feature(feature) and (feature == "map_tiles" or google.feature("map_tiles"))
NEARBY_RADIUS_M = float(APP["nearby"]["search_radius_m"])
GOOGLE_ROUTE_TTL_S = 300
DEPARTURE_WINDOW_S = float(APP["departure_window_days"]) * 86400
DUPLICATE_MERGE_M = float(APP["nearby"]["duplicate_merge_m"])
LAYERS_MAX_SQ_DEG = 0.05 * 0.05  # about 5 x 5 km: more than that is too many lamps to draw
ENDPOINT_LANDMARK_M = float(APP["explain"]["endpoint_landmark_m"])
REPORT_SNAP_M = float(config.scoring()["routing"]["max_snap_m"])


async def clean_expired_trips():
    while True:
        try:
            await asyncio.to_thread(trips.purge_expired)
        except Exception:
            logging.exception("Trip expiry cleanup failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app):
    cleanup = asyncio.create_task(clean_expired_trips())
    yield
    cleanup.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup


app = FastAPI(title="SafeHer API", version="0.1.0", lifespan=lifespan)
app.include_router(trips.router)
app.include_router(chatbot_router)


@app.middleware("http")
async def revalidate_web_assets(request, call_next):
    """Make browsers re-check the web app files on every load, so a demo never shows a stale build."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    if (request.url.path.startswith(("/api/trips", "/api/chat"))
            or request.url.path == "/live"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


# --- Community reports (user database) ------------------------------------------------------

REPORT_FIELDS = ("id", "lat", "lon", "category", "created_at", "verification")
_reports_cache: tuple[tuple, list] = ((), [])
_reports_lock = threading.Lock()


def _reports_version() -> tuple:
    """Changes whenever a report is added or removed (cheap: one indexed query)."""
    with userdb.database() as db:
        n, last = db.execute("SELECT count(*), max(id) FROM reports").fetchone()
    return (str(userdb.DB_FILE), n, last)


def _reports_for_planning() -> list[tuple[LatLon, sig.Report]]:
    """Parsed once per change of the reports table, not on every request."""
    global _reports_cache
    version = _reports_version()
    with _reports_lock:
        if _reports_cache[0] != version:
            with userdb.database() as db:
                rows = db.execute("SELECT lat, lon, category, created_at, verification FROM reports").fetchall()
            _reports_cache = (version, [((r["lat"], r["lon"]), sig.Report(r["category"],
                                         datetime.fromisoformat(r["created_at"]), r["verification"])) for r in rows])
        return _reports_cache[1]


# Planned trips, shared by /api/plan and /api/explain (which used to re-plan from scratch).
# Keyed by the exact inputs, including the reports file version, so a new report invalidates it.
_plan_cache: "OrderedDict[tuple, tuple[float, dict]]" = OrderedDict()
_plan_lock = threading.Lock()
PLAN_CACHE_TTL_S = 120
PLAN_CACHE_MAX = 256


def _planned(o: LatLon, d: LatLon, when: datetime) -> dict:
    corridor(o, d)  # refuse unsupported trips before spending a Google call on them
    key = (round(o[0], 6), round(o[1], 6), round(d[0], 6), round(d[1], 6),
           when.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat(), _reports_version(),
           g_on("routes"))
    with _plan_lock:
        hit = _plan_cache.get(key)
        if hit and time.time() - hit[0] < PLAN_CACHE_TTL_S:
            _plan_cache.move_to_end(key)
            return copy.deepcopy(hit[1])
    external, note = _google_routes(o, d)
    result = plan(o, d, when, _reports_for_planning(), external, float(GOOGLE["route_edge_match_m"]))
    if note:
        result["data_notes"].append(note)
    result["google_map"] = g_on("map_tiles")
    with _plan_lock:
        _plan_cache[key] = (time.time(), result)
        while len(_plan_cache) > PLAN_CACHE_MAX:
            _plan_cache.popitem(last=False)
    return copy.deepcopy(result)


# --- Models -------------------------------------------------------------------------------


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class PlanRequest(BaseModel):
    origin: Point
    destination: Point
    depart_at: datetime | None = None  # ISO 8601; naive = local time at the origin; omitted = now


class ExplainRequest(PlanRequest):
    lang: Literal[tuple(LANGUAGES)] = "en"  # type: ignore[valid-type]


class ReportRequest(BaseModel):
    lat: float
    lon: float
    category: Literal[tuple(sig.REPORT_SEVERITY)]  # type: ignore[valid-type]
    note: str | None = Field(default=None, max_length=280)


# --- Endpoints ----------------------------------------------------------------------------


@app.get("/api/cities")
def cities():
    """Supported cities: trips must start and end inside one of these."""
    return [
        {"id": c.id, "name": c.name, "center": c.center, "bbox": [c.west, c.south, c.east, c.north],
         "timezone": c.timezone, "default_trip": c.default_trip,
         "landmarks": [{"name": lm.name, "lat": lm.lat, "lon": lm.lon} for lm in c.landmarks]}
        for c in CITIES.values()
    ]


@app.get("/api/meta")
def meta():
    return {
        "weights_version": WEIGHTS_VERSION,
        "night_weights": NIGHT_WEIGHTS,
        "day_weights": DAY_WEIGHTS,
        "bands": [{"min": m, "label": label} for m, label in BANDS],
        "report_categories": list(sig.REPORT_SEVERITY),
        "helplines": APP["helplines"],
        "default_city": DEFAULT_CITY,
        "default_departure": APP["default_departure"],
        "basemaps": APP["basemaps"],
        "nearby_radius_m": NEARBY_RADIUS_M,
        "languages": APP["languages"],
        "disclaimer": DISCLAIMER,
        "google": {f: g_on(f) for f in GOOGLE["features"]},
        "google_autocomplete_min_chars": GOOGLE["autocomplete_min_chars"],
        "max_trip_km": APP["trips"]["max_trip_km"],
    }


@app.get("/api/search")
def search(q: str = Query(min_length=1, max_length=80), city: str | None = None,
           limit: int = APP["search"]["default_limit"], session: str | None = Query(default=None, max_length=64),
           lat: float | None = Query(default=None, ge=-90, le=90), lon: float | None = Query(default=None, ge=-180, le=180)):
    """Places, streets and landmarks. Searches `city` first (default: the one at lat/lon, else the
    default city); if nothing matches there, other cities' results come back flagged `other_city`."""
    if city and city not in CITIES:
        raise HTTPException(404, "unknown city")
    here = CITIES[city] if city else (city_at(lat, lon) if lat is not None and lon is not None else None) \
        or CITIES[DEFAULT_CITY]
    near = (lat, lon) if lat is not None and lon is not None else None
    results = place_search(q, [here.id], limit, near)
    if not results:
        results = [{**r, "other_city": True} for r in place_search(q, [c for c in CITIES if c != here.id], limit)]
    if g_on("autocomplete") and len(q.strip()) >= GOOGLE["autocomplete_min_chars"]:
        # Restricted to the city, so every Google suggestion is somewhere SafeHer can plan.
        try:
            preds = google.client().autocomplete(q.strip(), (here.south, here.west, here.north, here.east),
                                                 _session(session))
        except google.GoogleError as e:
            raise HTTPException(502, f"Google search failed: {e}") from e
        local_names = {r["name"].lower() for r in results if not r.get("other_city")}  # Google's are in this city
        for pr in preds:
            if pr["main"].lower() in local_names:
                continue
            results.append({"name": pr["main"], "kind": "google", "place_id": pr["place_id"], "source": "google",
                            "detail": " · ".join(x for x in (pr["secondary"], "Google") if x)})
    return results[: limit + 5]


def _parse_departure(depart_at: datetime | None, p: LatLon) -> datetime:
    city = city_at(*p)
    tz = city.tz if city else CITIES[DEFAULT_CITY].tz  # outside every city the trip is refused anyway
    now = datetime.now(timezone.utc)
    if depart_at is None:
        return now.astimezone(tz)
    when = depart_at if depart_at.tzinfo else depart_at.replace(tzinfo=tz)
    if abs((when - now).total_seconds()) > DEPARTURE_WINDOW_S:
        raise HTTPException(422, f"Departure time must be within {APP['departure_window_days']} days of today.")
    return when


@app.post("/api/plan")
def plan_trip(req: PlanRequest):
    o, d = (req.origin.lat, req.origin.lon), (req.destination.lat, req.destination.lon)
    when = _parse_departure(req.depart_at, o)
    try:
        return _planned(o, d, when)
    except (Unsupported, ValueError) as e:
        raise HTTPException(422, str(e)) from e


_route_cache: "OrderedDict[tuple, list]" = OrderedDict()


def _google_routes(o: LatLon, d: LatLon) -> tuple[list[dict], str | None]:
    """Google walking routes don't depend on departure time, so dragging the time slider reuses
    them (kept in memory for this process only, a few minutes at most)."""
    if not g_on("routes"):
        return [], None
    key = (round(o[0], 5), round(o[1], 5), round(d[0], 5), round(d[1], 5))
    hit = _route_cache.get(key)
    if hit and time.time() - hit[0] < GOOGLE_ROUTE_TTL_S:
        return hit[1], None
    try:
        routes = google.client().walking_routes(o, d)
    except google.GoogleError as e:
        raise HTTPException(502, f"Google routes failed: {e}") from e
    # Google's first route is the one it recommends; config decides how many to compare.
    external = [{"label": GOOGLE["route_label"] if i == 0 else f"{GOOGLE['route_label']} {i + 1}",
                 "source": "google", "points": r["points"]}
                for i, r in enumerate(routes[: int(GOOGLE["max_routes"])])]
    _route_cache[key] = (time.time(), external)
    while len(_route_cache) > 64:
        _route_cache.popitem(last=False)
    return external, None


_place_names: dict[str, set[str]] = {}


def _city_place_names(city_id: str) -> set[str]:
    """Every named place in the city: an explanation may only name the ones in its own facts."""
    if city_id not in _place_names:
        names = {(p["tags"].get("name:en") or p["tags"].get("name")) for p in store.places(city_id)}
        names |= {lm.name for lm in CITIES[city_id].landmarks}
        _place_names[city_id] = {n for n in names if n}
    return _place_names[city_id]


@app.post("/api/explain")
def explain_trip(req: ExplainRequest):
    """Plain-language comparison of the planned routes (Gemini when configured, else a template)."""
    o, d = (req.origin.lat, req.origin.lon), (req.destination.lat, req.destination.lon)
    when = _parse_departure(req.depart_at, o)
    try:
        p = _planned(o, d, when)  # same routes as the cards, including any Google route
    except (Unsupported, ValueError) as e:
        raise HTTPException(422, str(e)) from e
    # Landmarks at the start or end of the trip may be named; any other place not in the facts may not.
    city = p["area"]["city"]
    at_endpoints = {lm.name for lm in CITIES[city].landmarks
                    if min(haversine_m((lm.lat, lm.lon), o), haversine_m((lm.lat, lm.lon), d)) < ENDPOINT_LANDMARK_M}
    try:
        result = explain(p, req.lang, forbidden_names=_city_place_names(city) - at_endpoints)
    except ExplainFailed as e:
        raise HTTPException(502, str(e)) from e
    return {k: result[k] for k in ("text", "provider", "model", "lang", "checked")}


NearbyKind = Literal["police", "toilets", "hospital", "pharmacy", "open_places"]


@app.get("/api/nearby")
def nearby(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), kind: NearbyKind = Query(),
           depart_at: datetime | None = None,
           limit: int = APP["nearby"]["default_limit"]):
    when = _parse_departure(depart_at, (lat, lon))
    city = city_at(lat, lon)
    if city is None:
        raise HTTPException(422, f"SafeHer has street data for {supported_list()} only.")
    local = when.astimezone(city.tz)
    live = abs((when - datetime.now(timezone.utc)).total_seconds()) < 1800
    if g_on("nearby") and live:
        # Google's "open now" is live, so it's used for trips leaving about now.
        try:
            return _google_nearby(lat, lon, kind, local, limit, city, live)
        except google.GoogleError as e:
            raise HTTPException(502, f"Google nearby search failed: {e}") from e
    try:
        data = store.corridor_data(corridor((lat, lon), (lat, lon)))  # the city's data around this point
    except ValueError as e:  # e.g. fields or water with no mapped streets nearby
        raise HTTPException(422, f"No street data near this point ({e}).") from e
    index = data.safe_spaces if kind == "open_places" else data.stops
    out = []
    for dist, poi in index.within((lat, lon), NEARBY_RADIUS_M):
        if kind != "open_places" and poi.kind != kind:
            continue
        is_open, assumed = poi.is_open(local)
        if kind == "open_places" and not is_open:
            continue
        # OSM often maps one place twice (a point and a building outline): keep the nearer one.
        if poi.name and any(o["name"] == poi.name and haversine_m((o["lat"], o["lon"]), poi.latlon) < DUPLICATE_MERGE_M
                            for o in out):
            continue
        lamps = len(data.lamps.within(poi.latlon, sig.LAMP_RADIUS_M))
        out.append({"name": poi.name or KIND_LABELS.get(poi.kind, poi.kind).capitalize(),
                    "kind": poi.kind, "label": KIND_LABELS.get(poi.kind, poi.kind),
                    "lat": poi.lat, "lon": poi.lon, "distance_m": round(dist),
                    "open": is_open, "hours_assumed": assumed, "opening_hours": poi.opening_hours,
                    "lamps_nearby": lamps, "phone": poi.tags.get("phone") or poi.tags.get("contact:phone")})
        if len(out) >= limit:
            break
    return {"kind": kind, "at": local.isoformat(timespec="minutes"), "source": "openstreetmap", "results": out}


def _google_nearby(lat, lon, kind, local, limit, city, live=True):
    places = google.client().nearby(lat, lon, GOOGLE["nearby_types"][kind], NEARBY_RADIUS_M, limit * 2)
    out = []
    wanted = set(GOOGLE["nearby_types"][kind])
    for g in places:
        if not city.contains(g["lat"], g["lon"]):
            continue
        if not live:
            g = {**g, "open": None}  # Google only knows "open now", not open at another time
        if g["type"] not in wanted:  # e.g. a community centre that merely sits in a "police colony"
            continue
        if kind == "open_places" and g["open"] is not True:
            continue
        dist = haversine_m((lat, lon), (g["lat"], g["lon"]))
        if dist > NEARBY_RADIUS_M:
            continue
        our_kind = kind if kind != "open_places" else "open_places"
        out.append({"name": g["name"] or KIND_LABELS.get(our_kind, our_kind).capitalize(), "kind": our_kind,
                    "label": KIND_LABELS.get(our_kind, g["type"].replace("_", " ")), "lat": g["lat"],
                    "lon": g["lon"], "distance_m": round(dist), "open": g["open"], "hours_assumed": False,
                    "hours_source": "google", "opening_hours": None, "lamps_nearby": 0, "phone": g["phone"],
                    "place_id": g["place_id"]})
    out.sort(key=lambda x: x["distance_m"])
    return {"kind": kind, "at": local.isoformat(timespec="minutes"), "source": "google", "results": out[:limit]}


_SESSION_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,36}$")


def _session(token: str | None) -> str | None:
    """Google requires URL-safe base64 session tokens (≤36 chars); drop anything else rather than fail."""
    return token if token and _SESSION_TOKEN.match(token) else None


def _need(feature: str) -> None:
    if not g_on(feature):
        raise HTTPException(404, f"Google {feature.replace('_', ' ')} is not enabled")


def _image(content: bytes, content_type: str) -> Response:
    # Pass-through only: Google's terms don't allow caching tiles or images.
    return Response(content, media_type=content_type, headers={"Cache-Control": "no-store"})


@app.get("/api/google/tiles/{style}/{z}/{x}/{y}", include_in_schema=False)
def google_tile(style: Literal["light", "dark"], z: int = PathParam(ge=0, le=22), x: int = PathParam(ge=0),
                y: int = PathParam(ge=0)):
    _need("map_tiles")
    if x >= 2 ** z or y >= 2 ** z:
        raise HTTPException(404, "tile out of range")
    try:
        return _image(*google.client().tile(style, z, x, y))
    except google.GoogleError as e:
        raise HTTPException(502, f"Google tiles unavailable: {e}") from e


@app.get("/api/google/attribution")
def google_attribution(style: Literal["light", "dark"], zoom: int = Query(ge=0, le=22),
                       north: float = Query(ge=-90, le=90), south: float = Query(ge=-90, le=90),
                       east: float = Query(ge=-180, le=180), west: float = Query(ge=-180, le=180)):
    _need("map_tiles")
    try:
        return {"copyright": google.client().viewport_copyright(style, zoom, north, south, east, west)}
    except google.GoogleError as e:
        raise HTTPException(502, f"Google attribution unavailable: {e}") from e


@app.get("/api/google/place/{place_id}")
def google_place(place_id: str = PathParam(pattern=r"^[A-Za-z0-9_-]{10,300}$"),
                 session: str | None = Query(default=None, max_length=64)):
    _need("autocomplete")
    try:
        p = google.client().place(place_id, _session(session))
    except google.GoogleError as e:
        if e.status in (400, 404):  # malformed or unknown place ID: the client's mistake, not ours
            raise HTTPException(404, "Place not found.") from e
        raise HTTPException(502, f"Google place lookup failed: {e}") from e
    c = city_at(p["lat"], p["lon"])
    if c is None:
        raise HTTPException(422, f"{p['name'] or 'That place'} is outside {supported_list()}, "
                                 "where SafeHer has street data.")
    return {**p, "city": c.name, "city_id": c.id}


@app.get("/api/google/streetview/available")
def google_streetview_available(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    _need("streetview")
    try:
        return {"available": google.client().streetview_available(lat, lon, int(GOOGLE["streetview_radius_m"]))}
    except google.GoogleError as e:
        raise HTTPException(502, f"Street View unavailable: {e}") from e


@app.get("/api/google/streetview", include_in_schema=False)
def google_streetview(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
                      heading: float = Query(ge=-360, le=720)):
    _need("streetview")
    try:
        return _image(*google.client().streetview_image(lat, lon, heading, int(GOOGLE["streetview_radius_m"])))
    except google.GoogleError as e:
        raise HTTPException(502, f"Street View unavailable: {e}") from e


@app.get("/api/layers")
def layers(south: float = Query(ge=-90, le=90), west: float = Query(ge=-180, le=180),
           north: float = Query(ge=-90, le=90), east: float = Query(ge=-180, le=180)):
    """Map overlays for the visible map as GeoJSON: street lamps, police stations, washrooms, lit streets
    (OpenStreetMap) and NASA night-light pixels."""
    c = city_at((south + north) / 2, (west + east) / 2)
    if c is None:
        return {"type": "FeatureCollection", "features": []}
    if (north - south) * (east - west) > LAYERS_MAX_SQ_DEG or north < south or east < west:
        raise HTTPException(422, "Zoom in to see map layers.")
    kinds = {"street_lamp", "police", "toilets"}
    features = []
    for raw in store.places(c.id):
        if not (south <= raw["lat"] <= north and west <= raw["lon"] <= east):
            continue
        for poi in pois_from_osm([raw]):
            if poi.kind in kinds:
                features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [poi.lon, poi.lat]},
                                 "properties": {"kind": poi.kind,
                                                "name": poi.name or KIND_LABELS.get(poi.kind, poi.kind).capitalize()}})
    # Streets mapped as lit (OpenStreetMap), far more of them than individually mapped lamps.
    for la1, lo1, la2, lo2, name in store.lit_streets(c, south, west, north, east):
        features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[lo1, la1], [lo2, la2]]},
                         "properties": {"kind": "lit_street", "name": name or "Lit street"}})
    # NASA night lights: ~460 m pixels shaded by brightness rank in the city. Shows lit and dark areas,
    # not individual lamps.
    for px in store.night_light_pixels(c, south, west, north, east):
        s, n, w, e = px["lat"] - px["half_lat"], px["lat"] + px["half_lat"], px["lon"] - px["half_lon"], px["lon"] + px["half_lon"]
        features.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]},
                         "properties": {"kind": "night_light", "rank": round(px["rank"], 3),
                                        "radiance": round(px["radiance"], 1)}})
    return {"type": "FeatureCollection", "features": features}


@app.get("/api/reports")
def list_reports(city: str | None = None):
    if city and city not in CITIES:
        raise HTTPException(404, "unknown city")
    with userdb.database() as db:
        rows = db.execute("SELECT * FROM reports" + (" WHERE city = ?" if city else "") + " ORDER BY id",
                          (city,) if city else ()).fetchall()
    return [{k: r[k] for k in REPORT_FIELDS} for r in rows]


@app.post("/api/reports", status_code=201)
def create_report(req: ReportRequest):
    c = city_at(req.lat, req.lon)
    if c is None:
        raise HTTPException(422, f"Reports are only accepted in {supported_list()}.")
    # Privacy: store the nearest street point, not the exact tap.
    snapped = store.nearest_street_point(c, (req.lat, req.lon), REPORT_SNAP_M)
    if snapped is None:
        raise HTTPException(422, "Reports need to be on or near a street.")
    row = {"city": c.id, "lat": snapped[0], "lon": snapped[1], "category": req.category,
           "created_at": datetime.now(timezone.utc).isoformat(), "verification": "pending", "note": req.note}
    with userdb.database() as db:
        row["id"] = db.execute("INSERT INTO reports(city, lat, lon, category, created_at, verification, note) "
                               "VALUES (:city, :lat, :lon, :category, :created_at, :verification, :note)", row).lastrowid
    return {k: row[k] for k in REPORT_FIELDS}


# --- Web app ------------------------------------------------------------------------------

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/live", include_in_schema=False)
    def live_view():
        return FileResponse(WEB_DIR / "live.html")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")
