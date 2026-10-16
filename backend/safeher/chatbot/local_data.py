"""Read-only pilot data adapters. No Google data persistence or remote geocoder."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher

import networkx as nx

from ..areas import AREAS, area_for
from ..geo import haversine_m
from ..landmarks import LANDMARKS
from ..plan import WALK_M_PER_MIN
from ..routing import load_area
from ..pois import TRANSIT_KINDS

ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"
RESTRICTED_ACCESS = {"no", "private", "customers", "permit", "members"}


def snapshot(area):
    try:
        return json.loads((area.dir / "meta.json").read_text()).get("fetched_at")
    except (OSError, ValueError):
        return None


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"\bbangalore\b", "bengaluru", text)
    return " ".join(re.findall(r"\w+", text))


def local_search(query: str) -> list[dict]:
    """Rank names/aliases conservatively; return candidates, never auto-confirm fuzzy matches."""
    query = normalize(query)
    if not query:
        return []
    ranked = []
    for area in AREAS.values():
        city = normalize(area.city)
        other_cities = {normalize(a.city) for a in AREAS.values() if a.id != area.id}
        if any(c in query.split() for c in other_cities):
            continue
        needle = " ".join(w for w in query.split() if w not in {city, "new"})
        if len(needle) < 3:
            continue
        rows = [{"id": f"landmark:{area.id}:{i}", "name": name, "lat": lat, "lon": lon,
                 "source": "safeher_landmark", "aliases": [name]}
                for i, (name, lat, lon) in enumerate(LANDMARKS.get(area.id, []))]
        try:
            data = load_area(area.id)
        except (OSError, ValueError):
            data = None
        if data:
            for poi in data.pois:
                aliases = [poi.name, poi.tags.get("name"), poi.tags.get("name:en"),
                           poi.tags.get("alt_name"), poi.tags.get("short_name")]
                if poi.name and (poi.kind in TRANSIT_KINDS or
                                 str(poi.tags.get("access", "")).lower() not in RESTRICTED_ACCESS):
                    rows.append({"id": poi.osm, "name": poi.name, "lat": poi.lat, "lon": poi.lon,
                                 "source": "openstreetmap", "aliases": [a for a in aliases if a]})
        for row in rows:
            scores = []
            for alias in row.pop("aliases"):
                name = normalize(alias)
                ratio = SequenceMatcher(None, needle, name).ratio()
                if needle == name:
                    scores.append((1.0, "exact"))
                elif needle in name:
                    scores.append((0.95, "partial"))
                elif len(needle) >= 5 and ratio >= 0.82:
                    scores.append((ratio, "fuzzy"))
            if scores:
                score, match = max(scores)
                row.update(area_id=area.id, city=area.city, match=match, requires_confirmation=True,
                           snapshot_at=snapshot(area), attributions=[ATTRIBUTION])
                ranked.append((score, row))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["name"].casefold(), pair[1]["id"]))
    out = []
    for _, row in ranked:
        if any(normalize(p["name"]) == normalize(row["name"]) and
               haversine_m((p["lat"], p["lon"]), (row["lat"], row["lon"])) < 150 for p in out):
            continue
        out.append(row)
        if len(out) == 3:
            break
    return out


def poi_record(poi, area):
    now = datetime.now(area.tz)
    opened, assumed = poi.is_open(now)
    address = " ".join(str(poi.tags.get(key, "")) for key in
                       ("addr:housenumber", "addr:street", "addr:city")).strip() or None
    return {"id": poi.osm, "name": poi.name or poi.kind, "kind": poi.kind,
        "lat": poi.lat, "lon": poi.lon, "source": "openstreetmap", "area_id": area.id,
        "address": address, "phone": poi.tags.get("phone") or poi.tags.get("contact:phone"),
        "opening_hours": poi.opening_hours, "open_now": opened,
        "hours_basis": "assumed" if assumed else "mapped_not_verified",
        "snapshot_at": snapshot(area), "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "attributions": [ATTRIBUTION]}


def local_places(kind, lat, lon):
    area = area_for(lat, lon)
    if area is None:
        return []
    data = load_area(area.id)
    index = data.stops if kind == "toilets" else data.safe_spaces
    out = []
    for distance, poi in index.within((lat, lon), 2500):
        if poi.kind != kind or str(poi.tags.get("access", "")).lower() in RESTRICTED_ACCESS:
            continue
        if any(normalize(p["name"]) == normalize(poi.name or poi.kind) and poi.name and
               haversine_m((p["lat"], p["lon"]), poi.latlon) < 150 for p in out):
            continue
        out.append({**poi_record(poi, area), "distance_m": round(distance), "distance_basis": "straight_line"})
        if len(out) == 3:
            break
    return out


def local_details(place):
    area = AREAS.get(place.get("area_id"))
    if area is None:
        return {"error": "Local place is outside cached coverage."}
    poi = next((p for p in load_area(area.id).pois if p.osm == place["id"]), None)
    return {"details": poi_record(poi, area), "source": "openstreetmap"} if poi else {
        "error": "Local place no longer exists in this snapshot."}


def local_walking(origin, destination):
    area = area_for(*origin)
    if area is None or not area.contains(*destination):
        return {"source": "openstreetmap", "routes": [], "error": "outside_cached_coverage"}
    data = load_area(area.id)
    try:
        start, first = data.snap(origin)
        end, last = data.snap(destination)
        if start == end and origin != destination:
            return {"source": "openstreetmap", "routes": [], "error": "same_snap_node_ambiguous"}
        network = nx.shortest_path_length(data.graph, start, end, weight="length")
    except (ValueError, nx.NetworkXNoPath, nx.NodeNotFound):
        return {"source": "openstreetmap", "routes": [], "error": "no_mapped_walking_connection"}
    distance = network + first + last
    warning = ("Cached directed walking graph; straight-line endpoint connectors are approximate, "
               "not verified entrances/crossings. No safety score or live ETA.")
    return {"source": "openstreetmap", "scored": False, "snapshot_at": snapshot(area),
        "attributions": [ATTRIBUTION], "routes": [{"distance_m": round(distance),
            "duration_min": round(distance / WALK_M_PER_MIN, 1), "network_distance_m": round(network),
            "connector_distance_m": round(first + last), "walking_speed_m_per_min": WALK_M_PER_MIN,
            "distance_basis": "walking_network_with_approximate_connectors"}], "warning": warning}
