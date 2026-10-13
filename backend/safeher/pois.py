"""Points of interest from OSM: classification, opening hours, and a grid spatial index."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time
from functools import cached_property
from typing import Generic, Iterable, TypeVar

import opening_hours

from .geo import LatLon, haversine_m

# Kinds that count as "safe spaces" when open (spec §7.2), and what they're shown as.
SAFE_SPACE_KINDS = {
    "police", "hospital", "clinic", "pharmacy", "fuel", "convenience", "supermarket", "mall",
    "cafe", "restaurant",
}
TRANSIT_KINDS = {"bus_stop", "metro", "rail"}
STOP_KINDS = {"police", "toilets", "hospital", "pharmacy"}  # quick-action "safe stops" (§5.5)

# OSM has opening_hours for only ~5-15% of places in the pilot areas, so unknown hours fall back
# to typical hours for the kind. These are flagged `hours_assumed` and shown as such in the UI.
TYPICAL_HOURS: dict[str, tuple[time, time] | None] = {  # None = assume 24/7
    "police": None, "hospital": None, "metro": (time(5, 30), time(23, 30)),
    "fuel": (time(6), time(23)), "pharmacy": (time(9), time(22)), "clinic": (time(9), time(20)),
    "convenience": (time(8), time(22)), "supermarket": (time(9), time(22)), "mall": (time(10), time(22)),
    "cafe": (time(11), time(23)), "restaurant": (time(11), time(23)),
    "toilets": (time(6), time(22)), "bus_stop": (time(5), time(23)), "rail": (time(4), time(23, 59)),
}


def classify(tags: dict) -> str | None:
    amenity, shop = tags.get("amenity"), tags.get("shop")
    if amenity == "police" or tags.get("government") == "police":
        return "police"
    if amenity in {"hospital", "clinic", "pharmacy", "toilets", "fuel", "cafe"}:
        return amenity
    if amenity == "doctors":
        return "clinic"
    if amenity in {"restaurant", "fast_food"}:
        return "restaurant"
    if shop == "chemist":
        return "pharmacy"
    if shop in {"convenience", "supermarket"}:
        return shop
    if shop in {"mall", "department_store"}:
        return "mall"
    if tags.get("highway") == "street_lamp":
        return "street_lamp"
    if tags.get("highway") == "bus_stop":
        return "bus_stop"
    if tags.get("railway") == "subway_entrance" or tags.get("station") == "subway" or tags.get("subway") == "yes":
        return "metro"
    if tags.get("railway") in {"station", "halt"} or tags.get("public_transport") == "station":
        return "rail"
    return None  # banks, ATMs etc. are fetched but not used yet


@dataclass(frozen=True)
class Poi:
    osm: str
    kind: str
    lat: float
    lon: float
    name: str | None = None
    opening_hours: str | None = None
    tags: dict = field(default_factory=dict, compare=False, hash=False, repr=False)

    @property
    def latlon(self) -> LatLon:
        return (self.lat, self.lon)

    @cached_property
    def _parsed_hours(self):
        if not self.opening_hours:
            return None
        try:
            return opening_hours.OpeningHours(self.opening_hours)
        except Exception:  # malformed OSM values are common; fall back to typical hours
            return None

    def is_open(self, local: datetime) -> tuple[bool, bool]:
        """(open, assumed). `local` is wall-clock time in the area's timezone (naive or aware)."""
        naive = local.replace(tzinfo=None)
        parsed = self._parsed_hours
        if parsed is not None:
            try:
                return parsed.is_open(naive), False
            except Exception:
                pass
        hours = TYPICAL_HOURS.get(self.kind, (time(9), time(21)))
        if hours is None:
            return True, True
        start, end = hours
        t = naive.time()
        return (start <= t < end) if start <= end else (t >= start or t < end), True


def pois_from_osm(raw: Iterable[dict]) -> list[Poi]:
    out = []
    for r in raw:
        kind = classify(r["tags"])
        if kind:
            t = r["tags"]
            out.append(Poi(r["osm"], kind, r["lat"], r["lon"], t.get("name:en") or t.get("name"),
                           t.get("opening_hours"), t))
    return out


T = TypeVar("T")


class PointIndex(Generic[T]):
    """Uniform lat/lon grid. Good enough for a few thousand points in a city district."""

    def __init__(self, items: Iterable[tuple[LatLon, T]], cell_deg: float = 0.002):
        self.cell = cell_deg
        self.grid: dict[tuple[int, int], list[tuple[LatLon, T]]] = defaultdict(list)
        self.size = 0
        for p, item in items:
            self.grid[self._key(p)].append((p, item))
            self.size += 1

    def _key(self, p: LatLon) -> tuple[int, int]:
        return (math.floor(p[0] / self.cell), math.floor(p[1] / self.cell))

    def within(self, p: LatLon, radius_m: float) -> list[tuple[float, T]]:
        """Items within radius_m of p as (distance_m, item), nearest first."""
        dlat = radius_m / 111_000
        dlon = radius_m / (111_000 * max(0.1, math.cos(math.radians(p[0]))))
        lo = self._key((p[0] - dlat, p[1] - dlon))
        hi = self._key((p[0] + dlat, p[1] + dlon))
        found = []
        for i in range(lo[0], hi[0] + 1):
            for j in range(lo[1], hi[1] + 1):
                for q, item in self.grid.get((i, j), ()):
                    d = haversine_m(p, q)
                    if d <= radius_m:
                        found.append((d, item))
        found.sort(key=lambda x: x[0])
        return found

    def nearest(self, p: LatLon, max_m: float) -> tuple[float, T] | None:
        hits = self.within(p, max_m)
        return hits[0] if hits else None
