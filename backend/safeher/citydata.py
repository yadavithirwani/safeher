"""Whole-city street data from Geofabrik extracts, stored in DuckDB (config/cities.json).

    python -m safeher.citydata build [city ...]     one-off: extract → data/cities/<id>.duckdb
    python -m safeher.citydata info                 what's built

The extract is read once with pyosmium. We keep walkable street segments (the same rules as
OSMnx's "walk" network) and the same places the Overpass download keeps (osm.POI_SELECTORS), so a
corridor cut from here matches a downloaded one. At trip time, `build_corridor()` reads just the
corridor's rows (DuckDB file opened read-only, so every server worker can share it), builds and
simplifies the graph with OSMnx, and saves it in the normal area format.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import duckdb

from . import config
from .areas import Area
from .config import REPO_ROOT
from .geo import haversine_m

_C = config.load("cities")
SOURCE_DIR = REPO_ROOT / _C["source_dir"]
OUTPUT_DIR = REPO_ROOT / _C["output_dir"]
CITIES = {c["id"]: c for c in _C["cities"]}

# OSMnx 2.x "walk" network filter, written as Python checks.
_EXCLUDED_HIGHWAY = re.compile(r"abandoned|bus_guideway|construction|cycleway|motor|no|planned|platform|"
                               r"proposed|raceway|razed")
EDGE_TAGS = ("highway", "lit", "name", "sidewalk", "footway")


def walkable(tags) -> bool:
    hw = tags.get("highway")
    if not hw or _EXCLUDED_HIGHWAY.search(hw):
        return False
    if tags.get("area") == "yes" or tags.get("foot") == "no":
        return False
    if tags.get("service") == "private" or tags.get("access") == "private":
        return False
    return True


# The same place categories as the Overpass download (osm.POI_SELECTORS).
_AMENITY = {"police", "hospital", "clinic", "doctors", "pharmacy", "toilets", "fuel", "cafe", "restaurant",
            "fast_food", "bank", "atm"}
_SHOP = {"convenience", "supermarket", "chemist", "mall", "department_store"}


def useful_place(tags, is_node: bool) -> bool:
    if tags.get("amenity") in _AMENITY or tags.get("shop") in _SHOP:
        return True
    if tags.get("office") == "government" and tags.get("government") == "police":
        return True
    if is_node and tags.get("highway") in ("bus_stop", "street_lamp"):
        return True
    if tags.get("public_transport") == "station" or tags.get("railway") in ("station", "subway_entrance", "halt"):
        return True
    return False


def db_path(city_id: str) -> Path:
    return OUTPUT_DIR / f"{city_id}.duckdb"


def city_for_bbox(south: float, west: float, north: float, east: float) -> dict | None:
    """A built city whose bbox contains the whole corridor."""
    for c in CITIES.values():
        b = c["bbox"]
        if b["south"] <= south and b["west"] <= west and north <= b["north"] and east <= b["east"] and db_path(c["id"]).exists():
            return c
    return None


# --- Build ----------------------------------------------------------------------------------


def build(city_id: str) -> dict:
    import osmium
    import pandas as pd

    c = CITIES[city_id]
    b = c["bbox"]
    src = SOURCE_DIR / c["extract"]
    if not src.exists():
        raise FileNotFoundError(f"{src} missing: download it from https://download.geofabrik.de/")
    inside = lambda lat, lon: b["south"] <= lat <= b["north"] and b["west"] <= lon <= b["east"]  # noqa: E731
    seg_rows, poi_rows = [], []
    seen_pois = set()
    t0 = time.time()
    fp = osmium.FileProcessor(str(src)).with_locations()
    for obj in fp:
        if obj.is_node():
            if obj.tags and obj.location.valid() and inside(obj.location.lat, obj.location.lon) \
                    and useful_place(obj.tags, True):
                key = f"node/{obj.id}"
                if key not in seen_pois:
                    seen_pois.add(key)
                    poi_rows.append((key, obj.location.lat, obj.location.lon, json.dumps(dict(obj.tags))))
        elif obj.is_way():
            tags = obj.tags
            locs = [(n.ref, n.location.lat, n.location.lon) for n in obj.nodes if n.location.valid()]
            if len(locs) < 2:
                continue
            if useful_place(tags, False):
                lat = sum(p[1] for p in locs) / len(locs)
                lon = sum(p[2] for p in locs) / len(locs)
                if inside(lat, lon):
                    poi_rows.append((f"way/{obj.id}", lat, lon, json.dumps(dict(tags))))
            if not walkable(tags) or not any(inside(p[1], p[2]) for p in locs):
                continue
            t = {k: tags.get(k) for k in EDGE_TAGS}
            for (u, lat1, lon1), (v, lat2, lon2) in zip(locs, locs[1:]):
                seg_rows.append((obj.id, u, v, lat1, lon1, lat2, lon2,
                                 haversine_m((lat1, lon1), (lat2, lon2)), *t.values()))
    read_s = time.time() - t0
    segs = pd.DataFrame(seg_rows, columns=["way", "u", "v", "lat1", "lon1", "lat2", "lon2", "length", *EDGE_TAGS])
    pois = pd.DataFrame(poi_rows, columns=["osm", "lat", "lon", "tags"])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = db_path(city_id).with_suffix(".building")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.register("segs_df", segs)
    con.register("pois_df", pois)
    # Sorted by location so DuckDB's zone maps skip most of the file for a corridor query.
    con.execute("CREATE TABLE segments AS SELECT * FROM segs_df ORDER BY lat1, lon1")
    con.execute("CREATE TABLE pois AS SELECT * FROM pois_df ORDER BY lat, lon")
    meta = {"city": city_id, "source": c["extract"], "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "segments": len(segs), "pois": len(pois), "read_seconds": round(read_s),
            "attribution": "© OpenStreetMap contributors (ODbL), extract by Geofabrik"}
    con.execute("CREATE TABLE meta AS SELECT ? AS json", [json.dumps(meta)])
    con.close()
    tmp.replace(db_path(city_id))  # appears only when complete
    return meta


# --- Corridor --------------------------------------------------------------------------------


def build_corridor(city: dict, area: Area) -> dict:
    """Cut `area`'s bbox out of the city database and save it in the normal area format."""
    import networkx as nx
    import osmnx as ox

    con = duckdb.connect(str(db_path(city["id"])), read_only=True)
    try:
        q = ("SELECT * FROM segments WHERE lat1 BETWEEN ? AND ? AND lon1 BETWEEN ? AND ? "
             "AND lat2 BETWEEN ? AND ? AND lon2 BETWEEN ? AND ?")
        args = [area.south, area.north, area.west, area.east] * 2
        segs = con.execute(q, args).fetchall()
        cols = [d[0] for d in con.description]
        pois = con.execute("SELECT osm, lat, lon, tags FROM pois WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                           [area.south, area.north, area.west, area.east]).fetchall()
    finally:
        con.close()
    if not segs:
        raise ValueError("no walkable streets in this corridor")
    g = nx.MultiDiGraph(crs="epsg:4326")
    for row in segs:
        r = dict(zip(cols, row))
        attrs = {k: r[k] for k in EDGE_TAGS if r[k] is not None}
        g.add_node(r["u"], y=r["lat1"], x=r["lon1"])
        g.add_node(r["v"], y=r["lat2"], x=r["lon2"])
        # Walking ignores one-way rules: both directions, like OSMnx's walk network.
        g.add_edge(r["u"], r["v"], osmid=r["way"], length=r["length"], oneway=False, reversed=False, **attrs)
        g.add_edge(r["v"], r["u"], osmid=r["way"], length=r["length"], oneway=False, reversed=True, **attrs)
    g = ox.simplify_graph(g)
    largest = max(nx.strongly_connected_components(g), key=len)
    g = g.subgraph(largest).copy()
    area.dir.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(g, area.dir / "walk.graphml")
    (area.dir / "pois.json").write_text(json.dumps(
        [{"osm": o, "lat": lat, "lon": lon, "tags": json.loads(t)} for o, lat, lon, t in pois],
        ensure_ascii=False, separators=(",", ":")))
    return {"area": area.id, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "nodes": g.number_of_nodes(), "edges": g.number_of_edges(), "pois": len(pois),
            "source": f"© OpenStreetMap contributors, ODbL (local {city['name']} extract)"}


def info() -> None:
    for c in CITIES.values():
        p = db_path(c["id"])
        if not p.exists():
            print(f"{c['id']}: not built")
            continue
        con = duckdb.connect(str(p), read_only=True)
        meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
        con.close()
        print(f"{c['id']}: {meta['segments']:,} street segments, {meta['pois']:,} places, built {meta['built_at']}, "
              f"{p.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    cmd, *ids = sys.argv[1:] or ["info"]
    if cmd == "build":
        for cid in ids or list(CITIES):
            print(f"building {cid} …", flush=True)
            print(f"  {build(cid)}", flush=True)
    else:
        info()
