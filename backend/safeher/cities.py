"""Supported cities (config/cities.json) and trip corridors.

A trip is planned on a *corridor*: both points plus a margin, rounded so nearby trips share one, cut
from its city's data in data/safeher.duckdb (see store.py). Nothing here touches the disk.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from . import config
from .geo import LatLon, haversine_m


@dataclass(frozen=True)
class Landmark:
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class City:
    id: str
    name: str
    timezone: str
    south: float
    west: float
    north: float
    east: float
    landmarks: tuple[Landmark, ...] = field(default=(), compare=False)
    default_trip: tuple[str, str] | None = None
    districts: dict | None = field(default=None, compare=False, hash=False)  # OSM → NCRB mapping, if any
    city_stats: dict | None = field(default=None, compare=False, hash=False)  # city-wide police figures, if any

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def center(self) -> LatLon:
        return ((self.south + self.north) / 2, (self.west + self.east) / 2)

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east


@dataclass(frozen=True)
class Area:
    """A trip corridor inside one city. `id` identifies the corridor for caches."""

    id: str
    city: str
    name: str
    south: float
    west: float
    north: float
    east: float
    timezone: str

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.west, self.south, self.east, self.north)

    @property
    def center(self) -> LatLon:
        return ((self.south + self.north) / 2, (self.west + self.east) / 2)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east


def _load() -> dict[str, City]:
    out = {}
    for c in config.load("cities")["cities"]:
        b = c["bbox"]
        lms = tuple(Landmark(l["name"], float(l["lat"]), float(l["lon"])) for l in c.get("landmarks", []))
        city = City(c["id"], c["name"], c["timezone"], float(b["south"]), float(b["west"]), float(b["north"]),
                    float(b["east"]), lms, tuple(c["default_trip"]) if c.get("default_trip") else None, c.get("districts"),
                    c.get("city_stats"))
        for lm in lms:
            if not city.contains(lm.lat, lm.lon):
                raise RuntimeError(f"config/cities.json: landmark {lm.name!r} is outside {city.name}")
        if city.default_trip and not set(city.default_trip) <= {lm.name for lm in lms}:
            raise RuntimeError(f"config/cities.json: default_trip of {city.name} must name two of its landmarks")
        out[city.id] = city
    if not out:
        raise RuntimeError("config/cities.json defines no cities")
    return out


CITIES = _load()
_TRIPS = config.app()["trips"]
MAX_TRIP_M = float(_TRIPS["max_trip_km"]) * 1000
MARGIN_M = float(_TRIPS["corridor_margin_m"])


class Unsupported(ValueError):
    """The trip can't be planned: outside supported cities, across two cities, or too long to walk."""


def city_at(lat: float, lon: float) -> City | None:
    return next((c for c in CITIES.values() if c.contains(lat, lon)), None)


def supported_list() -> str:
    return " and ".join(c.name for c in CITIES.values())


def corridor(o: LatLon, d: LatLon, name: str | None = None) -> Area:
    """The corridor for a trip, or Unsupported with a message the user can act on."""
    co, cd = city_at(*o), city_at(*d)
    if co is None or cd is None:
        raise Unsupported(f"SafeHer has street data for {supported_list()} only.")
    if co != cd:
        raise Unsupported(f"Walks must start and end in the same city ({co.name} → {cd.name}).")
    dist = haversine_m(o, d)
    if dist > MAX_TRIP_M:
        raise Unsupported(f"That's {dist / 1000:.1f} km in a straight line; SafeHer plans walks up to "
                          f"{MAX_TRIP_M / 1000:.0f} km.")
    dlat = MARGIN_M / 111_320
    dlon = MARGIN_M / (111_320 * max(0.2, math.cos(math.radians((o[0] + d[0]) / 2))))
    down = lambda x: math.floor(x * 1000) / 1000  # noqa: E731  ~100 m steps, so nearby trips share a corridor
    up = lambda x: math.ceil(x * 1000) / 1000  # noqa: E731
    s, w = max(co.south, down(min(o[0], d[0]) - dlat)), max(co.west, down(min(o[1], d[1]) - dlon))
    n, e = min(co.north, up(max(o[0], d[0]) + dlat)), min(co.east, up(max(o[1], d[1]) + dlon))
    key = hashlib.sha1(f"{co.id},{s:.3f},{w:.3f},{n:.3f},{e:.3f}".encode()).hexdigest()[:10]
    return Area(f"{co.id}-{key}", co.id, name or co.name, s, w, n, e, co.timezone)
