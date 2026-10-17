"""Read-only local data for the chatbot: city-wide OpenStreetMap places and walking graphs from
data/safeher.duckdb (see store.py). No Google data persistence or remote geocoder."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from functools import lru_cache

import networkx as nx

from .. import store
from ..cities import CITIES, City, Unsupported, city_at, corridor
from ..geo import haversine_m
from ..plan import WALK_M_PER_MIN
from ..pois import SAFE_SPACE_KINDS, STOP_KINDS, Poi, PointIndex, pois_from_osm

ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"
# Words people put around a place name ("near Toit in Bengaluru"); never part of the match.
FILLER = {"in", "at", "near", "nearby", "around", "by", "the", "a", "an", "to", "from", "close", "new"}
# Kinds of place, not names: in "pharmacy near Toit" the place is Toit (nearby search handles the kind).
GENERIC = {"pharmacy", "chemist", "medical", "medical store", "police", "police station", "hospital", "clinic",
           "toilet", "toilets", "washroom", "public toilet", "cafe", "restaurant", "atm", "bank", "bus stop",
           "metro", "metro station", "supermarket", "temple", "school", "park", "parking"}
RESTRICTED_ACCESS = {"no", "private", "customers", "permit", "members"}


@dataclass
class CityData:
    """A city's places, indexed for the chatbot (built once per process)."""
    city: City
    rows: list[tuple[dict, list[str]]]  # search rows with their normalized aliases
    safe_spaces: PointIndex
    stops: PointIndex
    by_id: dict[str, Poi]
    name_count: Counter = field(default_factory=Counter)  # how many places share each normalized name


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"['’`]", "", text)  # "Bob's Bar" → "bobs bar", so it matches "bobs bar"
    text = re.sub(r"\bbangalore\b", "bengaluru", text)
    return " ".join(re.findall(r"\w+", text))


def snapshot(city: City) -> str | None:
    """When this city's data was built."""
    return next((s["built_at"] for s in store.sources() if s["city"] == city.id), None)


@lru_cache(maxsize=None)
def city_data(city_id: str) -> CityData:
    raw = store.places(city_id)
    return index_city(CITIES[city_id], pois_from_osm(raw), raw)


def _is_transit(tags: dict) -> bool:
    return bool(tags.get("public_transport") or tags.get("railway") or tags.get("highway") == "bus_stop")


def index_city(city: City, pois: list[Poi], raw: tuple[dict, ...] | None = None) -> CityData:
    """Search covers every named place (pubs, monuments, neighbourhoods too), not only scored kinds."""
    rows = [({"id": f"landmark:{city.id}:{i}", "name": lm.name, "lat": lm.lat, "lon": lm.lon,
              "source": "safeher_landmark"}, [normalize(lm.name)]) for i, lm in enumerate(city.landmarks)]
    named = ([(r["osm"], r["lat"], r["lon"], r["tags"]) for r in raw] if raw is not None
             else [(p.osm, p.lat, p.lon, {"name": p.name, **p.tags}) for p in pois])
    for osm, lat, lon, tags in named:
        name = tags.get("name") or tags.get("name:en")
        if name and (_is_transit(tags) or str(tags.get("access", "")).lower() not in RESTRICTED_ACCESS):
            aliases = {name, tags.get("name:en"), tags.get("alt_name"), tags.get("short_name")}
            rows.append(({"id": osm, "name": name, "lat": lat, "lon": lon, "source": "openstreetmap"},
                         [normalize(a) for a in aliases if a]))
    count = Counter(a for _, aliases in rows for a in set(aliases))
    return CityData(city, rows, PointIndex((p.latlon, p) for p in pois if p.kind in SAFE_SPACE_KINDS),
                    PointIndex((p.latlon, p) for p in pois if p.kind in STOP_KINDS), {p.osm: p for p in pois}, count)


def corridor_data(origin, destination):
    """The trip corridor's walking graph (raises Unsupported outside the supported cities)."""
    return store.corridor_data(corridor(origin, destination))


def _score(needle: str, name: str):
    if needle == name:
        return 1.0, "exact"
    if needle in name:
        return 0.95, "partial"
    if len(name) >= 4 and f" {name} " in f" {needle} ":
        return 0.9, "contained"  # "toit indiranagar" contains the place "toit"
    if len(needle) >= 5:
        m = SequenceMatcher(None, needle, name)
        if m.real_quick_ratio() >= 0.82 and m.quick_ratio() >= 0.82:  # cheap upper bounds first
            ratio = m.ratio()
            if ratio >= 0.82:
                return ratio, "fuzzy"
    return None


def local_search(query: str) -> list[dict]:
    """Rank names/aliases conservatively; return candidates, never auto-confirm fuzzy matches."""
    query = normalize(query)
    if not query:
        return []
    ranked = []
    for city in CITIES.values():
        name = normalize(city.name)
        other_cities = {normalize(c.name) for c in CITIES.values() if c.id != city.id}
        if any(c in query.split() for c in other_cities):
            continue
        needle = " ".join(w for w in query.split() if w != name and w not in FILLER)
        if len(needle) < 3:
            continue
        data = city_data(city.id)
        for row, aliases in data.rows:
            scores = [s for s in (_score(needle, a) for a in aliases) if s]
            # A name found inside the query ("toit indiranagar"): prefer the rarer one (one Toit,
            # many places called "Indiranagar" or "Pharmacy").
            scores = [(sc - 0.01 * min(data.name_count[a], 9) - (0.05 if a in GENERIC else 0), m)
                      if m == "contained" else (sc, m)
                      for (sc, m), a in zip(scores, [a for a in aliases if _score(needle, a)])]
            if scores:
                score, match = max(scores)
                ranked.append((score, {**row, "city_id": city.id, "city": city.name, "match": match,
                                       "requires_confirmation": True, "snapshot_at": snapshot(city),
                                       "attributions": [ATTRIBUTION]}))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["name"].casefold(), pair[1]["id"]))
    out = []
    for _, row in ranked:
        if any(normalize(p["name"]) == normalize(row["name"]) and
               haversine_m((p["lat"], p["lon"]), (row["lat"], row["lon"])) < 150 for p in out):
            continue
        out.append(row)
        if len(out) == 3:
            break
    for row in out:  # tells same-named places apart ("Bob's Bar" in Indiranagar vs Koramangala)
        row["neighbourhood"] = neighbourhood(row["city_id"], (row["lat"], row["lon"]))
    return out


def neighbourhood(city_id: str, p) -> str | None:
    from ..search import index_for
    try:
        return index_for(city_id).neighbourhood(p)
    except store.NotBuilt:
        return None


def poi_record(poi, city: City):
    now = datetime.now(city.tz)
    opened, assumed = poi.is_open(now)
    address = " ".join(str(poi.tags.get(key, "")) for key in
                       ("addr:housenumber", "addr:street", "addr:city")).strip() or None
    return {"id": poi.osm, "name": poi.name or poi.kind, "kind": poi.kind,
        "lat": poi.lat, "lon": poi.lon, "source": "openstreetmap", "city_id": city.id,
        "address": address, "phone": poi.tags.get("phone") or poi.tags.get("contact:phone"),
        "opening_hours": poi.opening_hours, "open_now": opened,
        "hours_basis": "assumed" if assumed else "mapped_not_verified",
        "snapshot_at": snapshot(city), "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "attributions": [ATTRIBUTION]}


def local_places(kind, lat, lon):
    city = city_at(lat, lon)
    if city is None:
        return []
    data = city_data(city.id)
    index = data.stops if kind == "toilets" else data.safe_spaces
    out = []
    for distance, poi in index.within((lat, lon), 2500):
        if poi.kind != kind or str(poi.tags.get("access", "")).lower() in RESTRICTED_ACCESS:
            continue
        if any(normalize(p["name"]) == normalize(poi.name or poi.kind) and poi.name and
               haversine_m((p["lat"], p["lon"]), poi.latlon) < 150 for p in out):
            continue
        out.append({**poi_record(poi, city), "distance_m": round(distance), "distance_basis": "straight_line"})
        if len(out) == 3:
            break
    return out


def local_details(place):
    city = CITIES.get(place.get("city_id"))
    if city is None:
        return {"error": "Local place is outside Delhi and Bengaluru."}
    poi = city_data(city.id).by_id.get(place["id"])
    return {"details": poi_record(poi, city), "source": "openstreetmap"} if poi else {
        "error": "Local place no longer exists in this data."}


def local_walking(origin, destination):
    try:
        data = corridor_data(origin, destination)
    except Unsupported:
        return {"source": "openstreetmap", "routes": [], "error": "outside_supported_cities"}
    try:
        start, first = data.snap(origin)
        end, last = data.snap(destination)
        if start == end and origin != destination:
            return {"source": "openstreetmap", "routes": [], "error": "same_snap_node_ambiguous"}
        network = nx.shortest_path_length(data.graph, start, end, weight="length")
    except (ValueError, nx.NetworkXNoPath, nx.NodeNotFound):
        return {"source": "openstreetmap", "routes": [], "error": "no_mapped_walking_connection"}
    distance = network + first + last
    warning = ("Walking graph from OpenStreetMap; straight-line endpoint connectors are approximate, "
               "not verified entrances/crossings. No safety score or live ETA.")
    return {"source": "openstreetmap", "scored": False, "snapshot_at": snapshot(CITIES[data.area.city]),
        "attributions": [ATTRIBUTION], "routes": [{"distance_m": round(distance),
            "duration_min": round(distance / WALK_M_PER_MIN, 1), "network_distance_m": round(network),
            "connector_distance_m": round(first + last), "walking_speed_m_per_min": WALK_M_PER_MIN,
            "distance_basis": "walking_network_with_approximate_connectors"}], "warning": warning}
