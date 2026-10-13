"""Download OpenStreetMap data for a demo area and cache it on disk.

Public Overpass servers are often overloaded, so every request tries several mirrors, and the
API only ever reads from the cache. Run `python -m safeher.fetch <area-id>` to (re)build it.
"""

from __future__ import annotations

import json
import time

import httpx

from .areas import Area

OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
USER_AGENT = "SafeHer/0.1 (hackathon prototype; github.com/yadavithirwani/safeher)"

# Everything the scoring needs besides the street graph: safe spaces, washrooms, transit, lamps.
POI_SELECTORS = (
    'nwr["amenity"~"^(police|hospital|clinic|doctors|pharmacy|toilets|fuel|cafe|restaurant|fast_food|bank|atm)$"]',
    'nwr["shop"~"^(convenience|supermarket|chemist|mall|department_store)$"]',
    'nwr["office"="government"]["government"="police"]',
    'node["highway"="bus_stop"]',
    'nwr["public_transport"="station"]',
    'nwr["railway"~"^(station|subway_entrance|halt)$"]',
    'node["highway"="street_lamp"]',
)


def poi_query(area: Area, timeout_s: int = 90) -> str:
    bbox = f"{area.south},{area.west},{area.north},{area.east}"
    body = "".join(f"{sel}({bbox});" for sel in POI_SELECTORS)
    return f"[out:json][timeout:{timeout_s}];({body});out center tags;"


def overpass(query: str, attempts_per_mirror: int = 2, timeout_s: float = 120.0) -> dict:
    errors: list[str] = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout_s) as client:
        for url in OVERPASS_MIRRORS:
            for attempt in range(attempts_per_mirror):
                try:
                    resp = client.post(url, data={"data": query})
                    if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/json"):
                        data = resp.json()
                        if "remark" in data and not data.get("elements"):
                            raise RuntimeError(data["remark"][:200])
                        return data
                    errors.append(f"{url}: HTTP {resp.status_code}")
                except (httpx.HTTPError, ValueError, RuntimeError) as e:
                    errors.append(f"{url}: {type(e).__name__}: {e}")
                time.sleep(2 * (attempt + 1))
    raise RuntimeError("all Overpass mirrors failed:\n  " + "\n  ".join(errors))


def fetch_pois(area: Area) -> list[dict]:
    data = overpass(poi_query(area))
    out = []
    for el in data["elements"]:
        lat = el.get("lat", el.get("center", {}).get("lat"))
        lon = el.get("lon", el.get("center", {}).get("lon"))
        if lat is None or lon is None:
            continue
        out.append({"osm": f"{el['type']}/{el['id']}", "lat": lat, "lon": lon, "tags": el.get("tags", {})})
    return out


def fetch_graph(area: Area):
    """Walking network with the `lit` tag kept on every edge."""
    import osmnx as ox

    ox.settings.useful_tags_way = sorted(set(ox.settings.useful_tags_way) | {"lit", "sidewalk", "footway"})
    ox.settings.requests_timeout = 180
    last_error: Exception | None = None
    for url in OVERPASS_MIRRORS:
        ox.settings.overpass_url = url.removesuffix("/interpreter")
        try:
            return ox.graph_from_bbox(area.bbox, network_type="walk", simplify=True, retain_all=False)
        except Exception as e:  # osmnx raises a variety of HTTP/response errors
            last_error = e
    raise RuntimeError(f"could not download walk graph: {last_error}")


def save_area(area: Area) -> dict:
    import osmnx as ox

    area.dir.mkdir(parents=True, exist_ok=True)
    graph = fetch_graph(area)
    ox.save_graphml(graph, area.dir / "walk.graphml")
    pois = fetch_pois(area)
    (area.dir / "pois.json").write_text(json.dumps(pois, ensure_ascii=False, separators=(",", ":")))
    meta = {
        "area": area.id,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "pois": len(pois),
        "source": "© OpenStreetMap contributors, ODbL",
    }
    (area.dir / "meta.json").write_text(json.dumps(meta, indent=2))
    return meta
