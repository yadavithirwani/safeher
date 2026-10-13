"""HTTP API + demo web app.  Run:  uvicorn safeher.api:app --reload   (from backend/)"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
import copy
import json
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import trips
from . import signals as sig
from . import config, google
from .areas import AREAS, area_for
from .config import REPO_ROOT
from .geo import LatLon, haversine_m
from .explain import LANGUAGES, explain
from .plan import DISCLAIMER, KIND_LABELS, plan
from .pois import SAFE_SPACE_KINDS
from .routing import load_area
from .search import search as place_search
from .scoring import BANDS, DAY_WEIGHTS, NIGHT_WEIGHTS, WEIGHTS_VERSION

WEB_DIR = REPO_ROOT / "app" / "web"
REPORTS_FILE = Path(__file__).resolve().parents[1] / "var" / "reports.jsonl"

APP = config.app()
GOOGLE = APP["google"]


def g_on(feature: str) -> bool:
    """A Google feature is usable only with its own switch AND the Google basemap: Google's terms
    don't allow Places or Routes content on a non-Google map."""
    return google.feature(feature) and (feature == "map_tiles" or google.feature("map_tiles"))
NEARBY_RADIUS_M = float(APP["nearby"]["search_radius_m"])
GOOGLE_ROUTE_TTL_S = 300
DEPARTURE_WINDOW_S = float(APP["departure_window_days"]) * 86400
DUPLICATE_MERGE_M = float(APP["nearby"]["duplicate_merge_m"])
ENDPOINT_LANDMARK_M = float(APP["explain"]["endpoint_landmark_m"])


def _area_list() -> str:
    return ", ".join(f"{a.name} ({a.city})" for a in AREAS.values())


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


@app.middleware("http")
async def revalidate_web_assets(request, call_next):
    """Make browsers re-check the web app files on every load, so a demo never shows a stale build."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    if request.url.path.startswith("/api/trips") or request.url.path == "/live":
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


# --- Community reports (file-backed for the prototype) -------------------------------------

_reports_lock = threading.Lock()


def _load_reports() -> list[dict]:
    if not REPORTS_FILE.exists():
        return []
    return [json.loads(line) for line in REPORTS_FILE.read_text().splitlines() if line.strip()]


_reports_cache: tuple[tuple, list] = ((), [])


def _reports_version() -> tuple:
    try:
        st = REPORTS_FILE.stat()
        return (str(REPORTS_FILE), st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        return (str(REPORTS_FILE), 0, 0)


def _reports_for_planning() -> list[tuple[LatLon, sig.Report]]:
    """Parsed once per change of the reports file, not on every request."""
    global _reports_cache
    version = _reports_version()
    if _reports_cache[0] != version:
        rows = [((r["lat"], r["lon"]), sig.Report(r["category"], datetime.fromisoformat(r["created_at"]),
                                                  r["verification"])) for r in _load_reports()]
        _reports_cache = (version, rows)
    return _reports_cache[1]


# Planned trips, shared by /api/plan and /api/explain (which used to re-plan from scratch).
# Keyed by the exact inputs, including the reports file version, so a new report invalidates it.
_plan_cache: "OrderedDict[tuple, tuple[float, dict]]" = OrderedDict()
_plan_lock = threading.Lock()
PLAN_CACHE_TTL_S = 120
PLAN_CACHE_MAX = 256


def _planned(o: LatLon, d: LatLon, when: datetime) -> dict:
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
    depart_at: datetime | None = None  # ISO 8601; naive = local time in the area; omitted = now


class ExplainRequest(PlanRequest):
    lang: Literal[tuple(LANGUAGES)] = "en"  # type: ignore[valid-type]


class ReportRequest(BaseModel):
    lat: float
    lon: float
    category: Literal[tuple(sig.REPORT_SEVERITY)]  # type: ignore[valid-type]
    note: str | None = Field(default=None, max_length=280)


# --- Endpoints ----------------------------------------------------------------------------


@app.get("/api/areas")
def areas():
    return [
        {"id": a.id, "city": a.city, "name": a.name, "center": a.center,
         "bbox": [a.west, a.south, a.east, a.north],
         "timezone": a.timezone, "default_trip": a.default_trip,
         "landmarks": [{"name": lm.name, "lat": lm.lat, "lon": lm.lon} for lm in a.landmarks]}
        for a in AREAS.values()
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
        "default_area": APP["default_area"] if APP["default_area"] in AREAS else next(iter(AREAS)),
        "default_departure": APP["default_departure"],
        "basemaps": APP["basemaps"],
        "nearby_radius_m": NEARBY_RADIUS_M,
        "languages": APP["languages"],
        "disclaimer": DISCLAIMER,
        "google": {f: g_on(f) for f in GOOGLE["features"]},
        "google_autocomplete_min_chars": GOOGLE["autocomplete_min_chars"],
    }


@app.get("/api/search")
def search(q: str = Query(min_length=1, max_length=80), area: str | None = None,
           limit: int = APP["search"]["default_limit"], session: str | None = Query(default=None, max_length=64)):
    """Places, streets and landmarks. With `area`, searches it first; if nothing matches there,
    results from the other demo areas come back flagged `other_area` so the app can offer a switch."""
    if area and area not in AREAS:
        raise HTTPException(404, "unknown area")
    first = [area] if area else list(AREAS)
    results = place_search(q, first, limit)
    if not results and area:
        results = [{**r, "other_area": True} for r in place_search(q, [a for a in AREAS if a != area], limit)]
    if area and g_on("autocomplete") and len(q.strip()) >= GOOGLE["autocomplete_min_chars"]:
        a = AREAS[area]
        try:
            preds = google.client().autocomplete(q.strip(), (a.south, a.west, a.north, a.east), session)
        except google.GoogleError:
            preds = []  # local results still work
        local_names = {r["name"].lower() for r in results}
        for pr in preds:
            if pr["main"].lower() in local_names:
                continue
            results.append({"name": pr["main"], "kind": "google", "place_id": pr["place_id"], "source": "google",
                            "detail": " · ".join(x for x in (pr["secondary"], "Google") if x), "area": area,
                            "city": a.city})
    return results[: limit + 5]


def _parse_departure(depart_at: datetime | None, p: LatLon) -> datetime:
    area = area_for(*p)
    if area is None:
        raise HTTPException(422, f"This location is outside the demo areas: {_area_list()}.")
    now = datetime.now(timezone.utc)
    if depart_at is None:
        return now.astimezone(area.tz)
    when = depart_at if depart_at.tzinfo else depart_at.replace(tzinfo=area.tz)
    if abs((when - now).total_seconds()) > DEPARTURE_WINDOW_S:
        raise HTTPException(422, f"Departure time must be within {APP['departure_window_days']} days of today.")
    return when


@app.post("/api/plan")
def plan_trip(req: PlanRequest):
    o, d = (req.origin.lat, req.origin.lon), (req.destination.lat, req.destination.lon)
    when = _parse_departure(req.depart_at, o)
    try:
        return _planned(o, d, when)
    except ValueError as e:
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
        return [], f"Google routes unavailable ({e}); showing SafeHer routes only."
    external = [{"label": GOOGLE["route_label"] if i == 0 else f"{GOOGLE['route_label']} {i + 1}",
                 "source": "google", "points": r["points"]} for i, r in enumerate(routes)]
    _route_cache[key] = (time.time(), external)
    while len(_route_cache) > 64:
        _route_cache.popitem(last=False)
    return external, None


_place_names: dict[str, set[str]] = {}


def _area_place_names(area_id: str) -> set[str]:
    """Every named place in the area: an explanation may only name the ones in its own facts."""
    if area_id not in _place_names:
        names = {p.name for p in load_area(area_id).pois if p.name}
        names |= {lm.name for lm in AREAS[area_id].landmarks}
        _place_names[area_id] = names
    return _place_names[area_id]


@app.post("/api/explain")
def explain_trip(req: ExplainRequest):
    """Plain-language comparison of the planned routes (Gemini when configured, else a template)."""
    o, d = (req.origin.lat, req.origin.lon), (req.destination.lat, req.destination.lon)
    when = _parse_departure(req.depart_at, o)
    try:
        p = _planned(o, d, when)  # same routes as the cards, including any Google route
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    # Landmarks at the start or end of the trip may be named; any other place not in the facts may not.
    at_endpoints = {lm.name for lm in AREAS[p["area"]["id"]].landmarks
                    if min(haversine_m((lm.lat, lm.lon), o), haversine_m((lm.lat, lm.lon), d)) < ENDPOINT_LANDMARK_M}
    result = explain(p, req.lang, forbidden_names=_area_place_names(p["area"]["id"]) - at_endpoints)
    return {k: result[k] for k in ("text", "provider", "model", "lang", "checked")}


NearbyKind = Literal["police", "toilets", "hospital", "pharmacy", "open_places"]


@app.get("/api/nearby")
def nearby(lat: float, lon: float, kind: NearbyKind, depart_at: datetime | None = None,
           limit: int = APP["nearby"]["default_limit"]):
    when = _parse_departure(depart_at, (lat, lon))
    data = load_area(area_for(lat, lon).id)
    local = when.astimezone(data.area.tz)
    if g_on("nearby") and abs((when - datetime.now(timezone.utc)).total_seconds()) < 1800:
        # Google's "open now" is live, so it's only used for trips leaving about now.
        try:
            return _google_nearby(lat, lon, kind, local, limit, data)
        except google.GoogleError:
            pass  # fall back to OpenStreetMap below
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


def _google_nearby(lat, lon, kind, local, limit, data):
    places = google.client().nearby(lat, lon, GOOGLE["nearby_types"][kind], NEARBY_RADIUS_M, limit * 2)
    out = []
    for g in places:
        if not data.area.contains(g["lat"], g["lon"]):
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
        p = google.client().place(place_id, session)
    except google.GoogleError as e:
        raise HTTPException(502, f"Google place lookup failed: {e}") from e
    a = area_for(p["lat"], p["lon"])
    if a is None:
        raise HTTPException(422, f"{p['name'] or 'That place'} is outside the demo areas: {_area_list()}.")
    return {**p, "area": a.id, "city": a.city}


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


@app.get("/api/layers/{area_id}")
def layers(area_id: str):
    """Static map overlays as GeoJSON: street lamps, police stations, washrooms."""
    if area_id not in AREAS:
        raise HTTPException(404, "unknown area")
    data = load_area(area_id)
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [p.lon, p.lat]},
         "properties": {"kind": p.kind, "name": p.name or KIND_LABELS.get(p.kind, p.kind).capitalize()}}
        for p in data.pois if p.kind in {"street_lamp", "police", "toilets"}
    ]
    return {"type": "FeatureCollection", "features": features}


@app.get("/api/reports")
def list_reports(area: str | None = None):
    rows = _load_reports()
    if area:
        a = AREAS.get(area)
        rows = [r for r in rows if a and a.contains(r["lat"], r["lon"])]
    return [{k: r[k] for k in ("id", "lat", "lon", "category", "created_at", "verification")} for r in rows]


@app.post("/api/reports", status_code=201)
def create_report(req: ReportRequest):
    area = area_for(req.lat, req.lon)
    if area is None:
        raise HTTPException(422, f"Reports are only accepted inside the demo areas: {_area_list()}.")
    data = load_area(area.id)
    try:
        node, dist = data.snap((req.lat, req.lon))  # privacy: store the street node, not the exact tap
    except ValueError as e:
        raise HTTPException(422, "Reports need to be on or near a street.") from e
    lat, lon = data.node_latlon(node)
    with _reports_lock:
        rows = _load_reports()
        row = {"id": len(rows) + 1, "lat": lat, "lon": lon, "category": req.category,
               "created_at": datetime.now(timezone.utc).isoformat(), "verification": "pending",
               "note": req.note}
        REPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with REPORTS_FILE.open("a") as f:
            f.write(json.dumps(row) + "\n")
    return {k: row[k] for k in ("id", "lat", "lon", "category", "created_at", "verification")}


# --- Web app ------------------------------------------------------------------------------

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/live", include_in_schema=False)
    def live_view():
        return FileResponse(WEB_DIR / "live.html")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")
