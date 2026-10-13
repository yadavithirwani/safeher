"""HTTP API + demo web app.  Run:  uvicorn safeher.api:app --reload   (from backend/)"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import signals as sig
from . import config
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
NEARBY_RADIUS_M = float(APP["nearby"]["search_radius_m"])
DUPLICATE_MERGE_M = float(APP["nearby"]["duplicate_merge_m"])
ENDPOINT_LANDMARK_M = float(APP["explain"]["endpoint_landmark_m"])


def _area_list() -> str:
    return ", ".join(f"{a.name} ({a.city})" for a in AREAS.values())


app = FastAPI(title="SafeHer API", version="0.1.0")


@app.middleware("http")
async def revalidate_web_assets(request, call_next):
    """Make browsers re-check the web app files on every load, so a demo never shows a stale build."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# --- Community reports (file-backed for the prototype) -------------------------------------

_reports_lock = threading.Lock()


def _load_reports() -> list[dict]:
    if not REPORTS_FILE.exists():
        return []
    return [json.loads(line) for line in REPORTS_FILE.read_text().splitlines() if line.strip()]


def _reports_for_planning() -> list[tuple[LatLon, sig.Report]]:
    return [((r["lat"], r["lon"]), sig.Report(r["category"], datetime.fromisoformat(r["created_at"]),
                                              r["verification"]))
            for r in _load_reports()]


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
    }


@app.get("/api/search")
def search(q: str = Query(min_length=1, max_length=80), area: str | None = None,
           limit: int = APP["search"]["default_limit"]):
    """Places, streets and landmarks. With `area`, searches it first; if nothing matches there,
    results from the other demo areas come back flagged `other_area` so the app can offer a switch."""
    if area and area not in AREAS:
        raise HTTPException(404, "unknown area")
    first = [area] if area else list(AREAS)
    results = place_search(q, first, limit)
    if not results and area:
        results = [{**r, "other_area": True} for r in place_search(q, [a for a in AREAS if a != area], limit)]
    return results


def _parse_departure(depart_at: datetime | None, p: LatLon) -> datetime:
    area = area_for(*p)
    if area is None:
        raise HTTPException(422, f"This location is outside the demo areas: {_area_list()}.")
    if depart_at is None:
        return datetime.now(timezone.utc).astimezone(area.tz)
    return depart_at if depart_at.tzinfo else depart_at.replace(tzinfo=area.tz)


@app.post("/api/plan")
def plan_trip(req: PlanRequest):
    o, d = (req.origin.lat, req.origin.lon), (req.destination.lat, req.destination.lon)
    when = _parse_departure(req.depart_at, o)
    try:
        return plan(o, d, when, _reports_for_planning())
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


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
        p = plan(o, d, when, _reports_for_planning())
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
    return {"kind": kind, "at": local.isoformat(timespec="minutes"), "results": out}


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
    node, dist = data.snap((req.lat, req.lon))  # privacy: store the street node, not the exact tap
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

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")
