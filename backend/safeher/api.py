"""HTTP API + demo web app.  Run:  uvicorn safeher.api:app --reload   (from backend/)"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import trips
from .chatbot.service import router as chatbot_router
from . import signals as sig
from .areas import AREAS, REPO_ROOT, area_for
from .landmarks import LANDMARKS
from .geo import LatLon, haversine_m
from .explain import LANGUAGES, explain
from .plan import DISCLAIMER, KIND_LABELS, plan
from .pois import SAFE_SPACE_KINDS
from .routing import load_area
from .scoring import BANDS, DAY_WEIGHTS, NIGHT_WEIGHTS, WEIGHTS_VERSION

WEB_DIR = REPO_ROOT / "app" / "web"
REPORTS_FILE = Path(__file__).resolve().parents[1] / "var" / "reports.jsonl"

HELPLINES = [
    {"number": "112", "label": "Emergency (police, fire, ambulance)"},
    {"number": "181", "label": "Women Helpline"},
    {"number": "1091", "label": "Women in Distress (police)"},
]


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
         "landmarks": [{"name": n, "lat": la, "lon": lo} for n, la, lo in LANDMARKS.get(a.id, [])]}
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
        "helplines": HELPLINES,
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/search")
def search(q: str = Query(min_length=1, max_length=80), area: str | None = None, limit: int = 8):
    ql = q.strip().lower()
    results: list[tuple[int, dict]] = []
    for a in AREAS.values():
        if area and a.id != area:
            continue
        for name, lat, lon in LANDMARKS.get(a.id, []):
            if ql in name.lower():
                results.append((0 if name.lower().startswith(ql) else 1,
                                {"name": name, "detail": f"{a.city} · landmark", "lat": lat, "lon": lon}))
        data = load_area(a.id)
        seen = set()
        for p in data.pois:
            if p.name and ql in p.name.lower() and p.name not in seen:
                seen.add(p.name)
                results.append((2 if p.name.lower().startswith(ql) else 3,
                                {"name": p.name, "detail": f"{a.city} · {KIND_LABELS.get(p.kind, p.kind)}",
                                 "lat": p.lat, "lon": p.lon}))
    results.sort(key=lambda x: (x[0], len(x[1]["name"])))
    return [r for _, r in results[:limit]]


def _parse_departure(depart_at: datetime | None, p: LatLon) -> datetime:
    area = area_for(*p)
    if area is None:
        raise HTTPException(422, "This location is outside the demo areas (Indiranagar–MG Road, Hauz Khas–Saket).")
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
        names |= {n for n, _, _ in LANDMARKS.get(area_id, [])}
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
    at_endpoints = {n for n, lat, lon in LANDMARKS.get(p["area"]["id"], [])
                    if min(haversine_m((lat, lon), o), haversine_m((lat, lon), d)) < 150}
    result = explain(p, req.lang, forbidden_names=_area_place_names(p["area"]["id"]) - at_endpoints)
    return {k: result[k] for k in ("text", "provider", "model", "lang", "checked")}


NearbyKind = Literal["police", "toilets", "hospital", "pharmacy", "open_places"]


@app.get("/api/nearby")
def nearby(lat: float, lon: float, kind: NearbyKind, depart_at: datetime | None = None, limit: int = 5):
    when = _parse_departure(depart_at, (lat, lon))
    data = load_area(area_for(lat, lon).id)
    local = when.astimezone(data.area.tz)
    index = data.safe_spaces if kind == "open_places" else data.stops
    out = []
    for dist, poi in index.within((lat, lon), 2500):
        if kind != "open_places" and poi.kind != kind:
            continue
        is_open, assumed = poi.is_open(local)
        if kind == "open_places" and not is_open:
            continue
        # OSM often maps one place twice (a point and a building outline): keep the nearer one.
        if poi.name and any(o["name"] == poi.name and haversine_m((o["lat"], o["lon"]), poi.latlon) < 150 for o in out):
            continue
        lamps = len(data.lamps.within(poi.latlon, 30))
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
        raise HTTPException(422, "Reports are only accepted inside the demo areas.")
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

    @app.get("/live", include_in_schema=False)
    def live_view():
        return FileResponse(WEB_DIR / "live.html")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")
