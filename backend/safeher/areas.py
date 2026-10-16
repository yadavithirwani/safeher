"""Demo areas, loaded from config/areas.json. Add an area there, then fetch its data with
`python -m safeher.fetch <area-id>`; no code changes needed."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config
from .config import REPO_ROOT

DATA_DIR = REPO_ROOT / "data" / "areas"
TRIP_DATA_DIR = DATA_DIR  # downloaded trip areas (data/areas/trip-*); tests point this elsewhere


@dataclass(frozen=True)
class Landmark:
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Area:
    id: str
    city: str
    name: str
    south: float
    west: float
    north: float
    east: float
    timezone: str
    ncrb_district: str | None = None  # NCRB registration circle covering the area, if sub-city data exists
    landmarks: tuple[Landmark, ...] = field(default=(), compare=False)
    default_trip: tuple[str, str] | None = None  # landmark names the app opens with

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(west, south, east, north) — the order OSMnx 2.x expects."""
        return (self.west, self.south, self.east, self.north)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.south + self.north) / 2, (self.west + self.east) / 2)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def dir(self) -> Path:
        return (TRIP_DATA_DIR if self.id.startswith(TRIP_PREFIX) else DATA_DIR) / self.id

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east


def _load() -> dict[str, Area]:
    areas = {}
    for a in config.areas():
        b = a["bbox"]
        landmarks = tuple(Landmark(l["name"], float(l["lat"]), float(l["lon"])) for l in a.get("landmarks", []))
        area = Area(a["id"], a["city"], a["name"], float(b["south"]), float(b["west"]), float(b["north"]),
                    float(b["east"]), a["timezone"], a.get("ncrb_district"), landmarks,
                    tuple(a["default_trip"]) if a.get("default_trip") else None)
        for lm in landmarks:
            if not area.contains(lm.lat, lm.lon):
                raise RuntimeError(f"config/areas.json: landmark {lm.name!r} is outside area {area.id!r}")
        if area.default_trip and not set(area.default_trip) <= {lm.name for lm in landmarks}:
            raise RuntimeError(f"config/areas.json: default_trip of {area.id!r} must name two of its landmarks")
        areas[area.id] = area
    if not areas:
        raise RuntimeError("config/areas.json defines no areas")
    return areas


AREAS = _load()  # configured demo areas (shown in the city switcher)

# --- Trip areas downloaded on demand ("go anywhere") --------------------------------------
# Each lives in data/areas/<id>/ with area.json (its definition) and, once complete, meta.json.
# They're read from disk, so every server worker sees areas another worker downloaded.

TRIP_PREFIX = "trip-"
_trip_areas: dict[str, Area] = {}
_tz_finder = None


def timezone_at(lat: float, lon: float) -> str:
    global _tz_finder
    if _tz_finder is None:
        from timezonefinder import TimezoneFinder
        _tz_finder = TimezoneFinder()
    return _tz_finder.timezone_at(lat=lat, lng=lon) or "UTC"


def area_to_json(a: Area) -> dict:
    return {"id": a.id, "city": a.city, "name": a.name, "timezone": a.timezone, "ncrb_district": a.ncrb_district,
            "bbox": {"south": a.south, "west": a.west, "north": a.north, "east": a.east}}


def area_from_json(d: dict) -> Area:
    b = d["bbox"]
    return Area(d["id"], d["city"], d["name"], float(b["south"]), float(b["west"]), float(b["north"]),
                float(b["east"]), d["timezone"], d.get("ncrb_district"))


def is_ready(a: Area) -> bool:
    """Downloaded completely (meta.json is written last)."""
    return (a.dir / "meta.json").exists() and (a.dir / "walk.graphml").exists()


def refresh_trip_areas() -> None:
    if not TRIP_DATA_DIR.exists():
        return
    for d in TRIP_DATA_DIR.glob(f"{TRIP_PREFIX}*/area.json"):
        if d.parent.name not in _trip_areas:
            try:
                _trip_areas[d.parent.name] = area_from_json(__import__("json").loads(d.read_text()))
            except (ValueError, KeyError):
                continue


def register_trip_area(a: Area) -> None:
    _trip_areas[a.id] = a


def get_area(area_id: str) -> Area:
    if area_id in AREAS:
        return AREAS[area_id]
    if area_id not in _trip_areas:
        refresh_trip_areas()
    return _trip_areas[area_id]


def all_areas() -> list[Area]:
    refresh_trip_areas()
    return list(AREAS.values()) + list(_trip_areas.values())


def area_for(lat: float, lon: float) -> Area | None:
    """The configured area containing the point, else a downloaded trip area containing it."""
    a = next((a for a in AREAS.values() if a.contains(lat, lon)), None)
    if a is None:
        refresh_trip_areas()
        a = next((a for a in _trip_areas.values() if a.contains(lat, lon) and is_ready(a)), None)
    return a


def area_covering(o: tuple[float, float], d: tuple[float, float]) -> Area | None:
    """The smallest ready area containing both points (configured areas first)."""
    for pool in (list(AREAS.values()), None):
        if pool is None:
            refresh_trip_areas()
            pool = [a for a in _trip_areas.values() if is_ready(a)]
        hits = [a for a in pool if a.contains(*o) and a.contains(*d)]
        if hits:
            return min(hits, key=lambda a: (a.north - a.south) * (a.east - a.west))
    return None
