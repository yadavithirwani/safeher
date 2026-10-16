"""Place and street search within the demo areas, built from the cached OSM data (works offline).

Matches every named place in pois.json (not only the kinds used for scoring) and every named
street in the walking graph. Results with the same name are told apart by the nearest street,
e.g. "Starbucks · café · 100 Feet Road".
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from .areas import AREAS
from .geo import LatLon, haversine_m, interpolate
from .pois import PointIndex
from .routing import load_area

STREET_CONTEXT_M = 120  # how far to look for a street name to describe a place's location

# Readable labels for OSM tag values; anything else is shown with underscores replaced.
LABELS = {
    "bus_stop": "bus stop", "subway_entrance": "metro entrance", "fast_food": "fast food", "toilets": "washroom",
    "atm": "ATM", "cafe": "café", "station": "station", "subway": "metro station", "doctors": "clinic", "department_store": "department store", "street_lamp": "street lamp",
}
PLACE_KEYS = ("amenity", "shop", "railway", "public_transport", "highway", "office", "leisure", "tourism",
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
            return LABELS.get(v, v.replace("_", " "))
    return "place"


@dataclass
class AreaIndex:
    entries: list[Entry]
    streets: PointIndex[str]  # sample points along named streets, for "· <street>" context

    def street_near(self, p: LatLon) -> str | None:
        hit = self.streets.nearest(p, STREET_CONTEXT_M)
        return hit[1] if hit else None


@lru_cache(maxsize=8)
def index_for(area_id: str) -> AreaIndex:
    area = AREAS[area_id]
    data = load_area(area_id)
    entries: list[Entry] = [Entry(lm.name, "landmark", lm.lat, lm.lon, 0) for lm in area.landmarks]

    # Every named place in the snapshot, de-duplicated (OSM often maps a place as point + outline).
    seen: list[tuple[str, LatLon]] = []
    for raw in json.loads((area.dir / "pois.json").read_text()):
        t = raw["tags"]
        name = t.get("name:en") or t.get("name")
        if not name or t.get("highway") == "street_lamp":
            continue
        p = (raw["lat"], raw["lon"])
        if any(n == name and haversine_m(q, p) < 80 for n, q in seen):
            continue
        seen.append((name, p))
        entries.append(Entry(name, _label(t), p[0], p[1], 1))

    # Named streets: one entry per name, placed at the middle of its longest piece.
    longest: dict[str, tuple[float, LatLon]] = {}
    street_pts: list[tuple[LatLon, str]] = []
    for u, v, d in data.graph.edges(data=True):
        names = d.get("name")
        if not names:
            continue
        for name in (names if isinstance(names, list) else [names]):
            a, b = data.node_latlon(u), data.node_latlon(v)
            mid = interpolate(a, b, 0.5)
            street_pts.append((mid, name))
            if d["length"] > longest.get(name, (0.0, mid))[0]:
                longest[name] = (d["length"], mid)
    entries += [Entry(n, "street", p[0], p[1], 2) for n, (_, p) in longest.items()]
    return AreaIndex(entries, PointIndex(street_pts))


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


def search(query: str, area_ids: list[str], limit: int) -> list[dict]:
    hits: list[tuple[tuple, dict]] = []
    for area_id in area_ids:
        idx = index_for(area_id)
        city = AREAS[area_id].city
        names = {}
        for e in idx.entries:
            rank = _match_rank(query, e.name)
            if rank is None:
                continue
            names.setdefault(e.name.lower(), []).append(e)
            hits.append(((rank, e.priority, len(e.name)), {"name": e.name, "kind": e.kind, "lat": e.lat,
                                                             "lon": e.lon, "city": city, "area": area_id}))
        # Same name, several places: add the street each one is on so people can tell them apart.
        for _, h in hits:
            if h["area"] == area_id and h["kind"] != "street" and len(names.get(h["name"].lower(), [])) > 1:
                street = idx.street_near((h["lat"], h["lon"]))
                h["street"] = street if street and street.lower() != h["name"].lower() else None
    hits.sort(key=lambda x: x[0])
    out = []
    for _, h in hits[:limit]:
        h["detail"] = " · ".join(x for x in (h["kind"], h.get("street"), h["city"]) if x)
        out.append(h)
    return out
