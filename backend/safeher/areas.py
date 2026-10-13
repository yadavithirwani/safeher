"""Demo areas, loaded from config/areas.json. Add an area there, then fetch its data with
`python -m safeher.fetch <area-id>`; no code changes needed."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config
from .config import REPO_ROOT

DATA_DIR = REPO_ROOT / "data" / "areas"


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
        return DATA_DIR / self.id

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


AREAS = _load()


def area_for(lat: float, lon: float) -> Area | None:
    return next((a for a in AREAS.values() if a.contains(lat, lon)), None)
