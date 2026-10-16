"""Place and street search across the supported cities, from data/safeher.duckdb (works offline).

Matches every named place (not only the kinds used for scoring) and every named street. Results with the same name are told apart by the nearest street,
e.g. "Starbucks · café · 100 Feet Road".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from . import store
from .cities import CITIES
from .geo import LatLon, haversine_m
from .pois import PointIndex

STREET_CONTEXT_M = 120  # how far to look for a street name to describe a place's location
NEIGHBOURHOOD_M = 2500  # how far to look for a neighbourhood name ("Indiranagar") for a result
NEIGHBOURHOODS = {"suburb", "neighbourhood", "quarter", "locality", "village", "town", "hamlet"}

# Readable labels for OSM tag values; anything else is shown with underscores replaced.
LABELS = {
    "bus_stop": "bus stop", "subway_entrance": "metro entrance", "fast_food": "fast food", "toilets": "washroom",
    "atm": "ATM", "cafe": "café", "station": "station", "subway": "metro station", "doctors": "clinic", "department_store": "department store", "street_lamp": "street lamp",
}
PLACE_KEYS = ("place", "amenity", "shop", "railway", "public_transport", "highway", "office", "leisure", "tourism",
              "government")


@dataclass(frozen=True)
class Entry:
    name: str
    kind: str  # human label: "café", "street", "metro entrance", "landmark"…
    lat: float
    lon: float
    priority: int  # lower sorts first for equally good matches: landmarks, then places, then streets


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode() or text
    text = re.sub(r"['’`]", "", text.lower())  # "Bob's" → "bobs", so a query without the apostrophe matches
    return re.sub(r"[^\w\s]", " ", text).strip()


def _label(tags: dict) -> str:
    for k in PLACE_KEYS:
        v = tags.get(k)
        if v and v not in ("yes", "platform", "stop_position"):
            if k == "government" and v == "police":
                return "police station"
            if k == "place":
                return "area"
            return LABELS.get(v, v.replace("_", " "))
    return "place"


@dataclass
class CityIndex:
    entries: list[Entry]
    neighbourhoods: PointIndex[str]

    def neighbourhood(self, p: LatLon) -> str | None:
        hit = self.neighbourhoods.nearest(p, NEIGHBOURHOOD_M)
        return hit[1] if hit else None


@lru_cache(maxsize=None)
def index_for(city_id: str) -> CityIndex:
    city = CITIES[city_id]
    entries: list[Entry] = [Entry(lm.name, "landmark", lm.lat, lm.lon, 0) for lm in city.landmarks]

    # Every named place, de-duplicated (OSM often maps a place as point + outline).
    seen: dict[str, list[LatLon]] = {}
    hoods: list[tuple[LatLon, str]] = []
    for raw in store.places(city_id):
        t = raw["tags"]
        name = t.get("name:en") or t.get("name")
        if not name or t.get("highway") == "street_lamp":
            continue
        p = (raw["lat"], raw["lon"])
        if any(haversine_m(q, p) < 80 for q in seen.get(name, ())):
            continue
        seen.setdefault(name, []).append(p)
        entries.append(Entry(name, _label(t), p[0], p[1], 1))
        if t.get("place") in NEIGHBOURHOODS:
            hoods.append((p, name))

    # Named streets: one entry per name per ~2 km (see store.street_names).
    entries += [Entry(n, "street", lat, lon, 2) for n, lat, lon in store.street_names(city_id)]
    return CityIndex(entries, PointIndex(hoods, cell_deg=0.02))


def _match_rank(query: str, name: str) -> int | None:
    """0 exact, 1 name starts with query, 2 every query word starts a word in name, 3 substring."""
    q, n = _norm(query), _norm(name)
    if not q:
        return None
    if n == q:
        return 0
    if n.startswith(q):
        return 1
    words = n.split()
    if all(any(w.startswith(t) for w in words) for t in q.split()):
        return 2
    if q in n:
        return 3
    return None


def search(query: str, city_ids: list[str], limit: int, near: LatLon | None = None) -> list[dict]:
    """Best matches first; equally good matches nearest to `near` (where the user is looking) first."""
    hits: list[tuple[tuple, dict]] = []
    for city_id in city_ids:
        city = CITIES[city_id]
        names: dict[str, int] = {}
        found = []
        for e in index_for(city_id).entries:
            rank = _match_rank(query, e.name)
            if rank is None:
                continue
            names[e.name.lower()] = names.get(e.name.lower(), 0) + 1
            km = round(haversine_m(near, (e.lat, e.lon)) / 1000) if near else 0
            found.append(((rank, e.priority, km, len(e.name)), {"name": e.name, "kind": e.kind, "lat": e.lat,
                                                             "lon": e.lon, "city": city.name, "city_id": city_id}))
        hits += found
    hits.sort(key=lambda x: x[0])
    out = []
    for _, h in hits[:limit]:
        # Same name, several places: add the street each one is on so people can tell them apart.
        if h["kind"] != "street" and names_count(hits, h) > 1:
            street = store.street_near(CITIES[h["city_id"]], (h["lat"], h["lon"]), STREET_CONTEXT_M)
            h["street"] = street if street and street.lower() != h["name"].lower() else None
        hood = index_for(h["city_id"]).neighbourhood((h["lat"], h["lon"])) if h["kind"] != "area" else None
        h["neighbourhood"] = hood if hood and hood.lower() not in h["name"].lower() else None
        h["detail"] = " · ".join(x for x in (h["kind"], h.get("street"), h["neighbourhood"], h["city"]) if x)
        out.append(h)
    return out


def names_count(hits: list, h: dict) -> int:
    n = h["name"].lower()
    return sum(1 for _, x in hits if x["city_id"] == h["city_id"] and x["name"].lower() == n)
