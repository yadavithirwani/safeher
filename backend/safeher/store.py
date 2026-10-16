"""Read-only access to data/safeher.duckdb (built by `python -m safeher.data build`).

Every server worker opens the file read-only, so they share it safely. Trip corridors are built in
memory from the rows inside their bbox (about a second) and cached; nothing is written to disk.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from functools import lru_cache

import duckdb
import numpy as np

from . import config
from .cities import Area, City
from .config import REPO_ROOT
from .geo import LatLon
from .pois import SAFE_SPACE_KINDS, STOP_KINDS, TRANSIT_KINDS, Poi, PointIndex, pois_from_osm

DB_PATH = REPO_ROOT / config.load("cities")["output"]
_local = threading.local()
_CACHE_MAX = int(config.app()["trips"]["corridor_cache_size"])
_corridors: "OrderedDict[str, object]" = OrderedDict()
_corridor_lock = threading.Lock()


class NotBuilt(RuntimeError):
    pass


def con() -> duckdb.DuckDBPyConnection:
    """One read-only connection per thread (DuckDB connections aren't shared across threads)."""
    c = getattr(_local, "con", None)
    if c is None:
        if not DB_PATH.exists():
            raise NotBuilt(f"{DB_PATH.name} is missing: run `python -m safeher.data build` in backend/")
        c = duckdb.connect(str(DB_PATH), read_only=True)
        _local.con = c
    return c


def available() -> bool:
    return DB_PATH.exists()


# --- City-wide data (cached per process) ------------------------------------------------------


@lru_cache(maxsize=None)
def places(city_id: str) -> tuple[dict, ...]:
    rows = con().execute("SELECT osm, lat, lon, tags FROM places WHERE city = ?", [city_id]).fetchall()
    return tuple({"osm": o, "lat": lat, "lon": lon, "tags": json.loads(t)} for o, lat, lon, t in rows)


@lru_cache(maxsize=None)
def street_names(city_id: str) -> tuple[tuple[str, float, float], ...]:
    """One point per street name per ~2 km cell (the middle of its longest segment there), so a common
    name like "100 Feet Road" is findable in each neighbourhood it runs through."""
    rows = con().execute("""SELECT name, arg_max((lat1 + lat2) / 2, length), arg_max((lon1 + lon2) / 2, length)
                            FROM segments WHERE city = ? AND name IS NOT NULL
                            GROUP BY name, floor(lat1 / 0.02), floor(lon1 / 0.02)""", [city_id]).fetchall()
    return tuple(rows)


@lru_cache(maxsize=None)
def night_lights(city_id: str):
    """The city's NASA night-light grid; brightness is ranked city-wide."""
    from .viirs import ViirsGrid

    rows = con().execute("SELECT lat, lon, radiance FROM night_lights WHERE city = ?", [city_id]).fetchall()
    if not rows:
        return None
    lats = sorted({r[0] for r in rows}, reverse=True)
    lons = sorted({r[1] for r in rows})
    li, lo = {v: i for i, v in enumerate(lats)}, {v: j for j, v in enumerate(lons)}
    grid = np.full((len(lats), len(lons)), np.nan)
    for lat, lon, v in rows:
        grid[li[lat], lo[lon]] = v
    return ViirsGrid(grid, np.array(lats), np.array(lons), int(config.load("cities")["night_lights"]["year"]))


@lru_cache(maxsize=None)
def _districts(city_id: str) -> tuple:
    from shapely import prepared, wkt

    rows = con().execute("SELECT name, wkt FROM districts WHERE city = ?", [city_id]).fetchall()
    return tuple((name, prepared.prep(g), g) for name, g in ((n, wkt.loads(w)) for n, w in rows))


@lru_cache(maxsize=None)
def _incidents(city_id: str) -> dict[str, tuple[float, float, float, int, int]]:
    rows = con().execute("SELECT district, avg_cases, percentile, confidence FROM incidents WHERE city = ? "
                         "ORDER BY avg_cases", [city_id]).fetchall()
    return {d: (avg, pct, conf, rank + 1, len(rows)) for rank, (d, avg, pct, conf) in enumerate(rows)}


def district_at(city: City, p: LatLon) -> str | None:
    from shapely.geometry import Point

    pt = Point(p[1], p[0])
    return next((name for name, prep, _ in _districts(city.id) if prep.contains(pt)), None)


def incident_at(city: City, p: LatLon) -> tuple[float, float, str] | None:
    """(city percentile, confidence, note) from NCRB for the police district at p, if mapped."""
    if not city.districts:
        return None
    osm_name = district_at(city, p)
    ncrb = city.districts["osm_to_ncrb"].get(osm_name or "")
    hit = _incidents(city.id).get(ncrb or "")
    if not hit:
        return None
    avg, pct, conf, rank, total = hit
    return pct, conf, (f"{ncrb} police district ({osm_name}): {avg:.0f} public-space cases/year, "
                       f"{rank} of {total} in {city.name} (1 = fewest)")


# --- Corridors ------------------------------------------------------------------------------------


def corridor_data(area: Area):
    """Walking graph + places for a corridor, cached in memory (built from DuckDB in about a second)."""
    with _corridor_lock:
        hit = _corridors.get(area.id)
        if hit is not None:
            _corridors.move_to_end(area.id)
            return hit
    data = _build(area)
    with _corridor_lock:
        _corridors[area.id] = data
        while len(_corridors) > _CACHE_MAX:
            _corridors.popitem(last=False)
    return data


def _build(area: Area):
    import networkx as nx
    import osmnx as ox

    from .routing import AreaData

    args = [area.city, area.south, area.north, area.west, area.east, area.south, area.north, area.west, area.east]
    segs = con().execute("""SELECT way, u, v, lat1, lon1, lat2, lon2, length, highway, lit, name, sidewalk, footway
                            FROM segments WHERE city = ? AND lat1 BETWEEN ? AND ? AND lon1 BETWEEN ? AND ?
                            AND lat2 BETWEEN ? AND ? AND lon2 BETWEEN ? AND ?""", args).fetchall()
    if not segs:
        raise ValueError("no walkable streets here")
    g = nx.MultiDiGraph(crs="epsg:4326")
    keys = ("highway", "lit", "name", "sidewalk", "footway")
    for way, u, v, la1, lo1, la2, lo2, length, *tags in segs:
        attrs = {k: t for k, t in zip(keys, tags) if t is not None}
        g.add_node(u, y=la1, x=lo1)
        g.add_node(v, y=la2, x=lo2)
        # Walking ignores one-way rules: both directions, like OSMnx's walk network.
        g.add_edge(u, v, osmid=way, length=length, oneway=False, reversed=False, **attrs)
        g.add_edge(v, u, osmid=way, length=length, oneway=False, reversed=True, **attrs)
    g = ox.simplify_graph(g)
    g = g.subgraph(max(nx.strongly_connected_components(g), key=len)).copy()
    rows = [p for p in places(area.city) if area.contains(p["lat"], p["lon"])]
    pois = pois_from_osm(rows)

    def index(kinds: set[str]) -> PointIndex[Poi]:
        return PointIndex((p.latlon, p) for p in pois if p.kind in kinds)

    return AreaData(area=area, graph=g, nodes=PointIndex(((d["y"], d["x"]), n) for n, d in g.nodes(data=True)),
                    pois=pois, lamps=index({"street_lamp"}), transit=index(TRANSIT_KINDS),
                    safe_spaces=index(SAFE_SPACE_KINDS), stops=index(STOP_KINDS), viirs=night_lights(area.city))


def nearest_street_point(city: City, p: LatLon, max_m: float) -> LatLon | None:
    """Closest street point to p within max_m (used to store reports at a street, not the exact tap)."""
    d = max_m / 111_320
    rows = con().execute("""SELECT lat1, lon1 FROM segments WHERE city = ? AND lat1 BETWEEN ? AND ?
                            AND lon1 BETWEEN ? AND ?""", [city.id, p[0] - d, p[0] + d, p[1] - d * 1.1, p[1] + d * 1.1]).fetchall()
    from .geo import haversine_m
    best = min(rows, key=lambda r: haversine_m(p, r), default=None)
    return best if best and haversine_m(p, best) <= max_m else None


def sources() -> list[dict]:
    rows = con().execute("SELECT city, source, detail, built_at FROM sources ORDER BY city, source").fetchall()
    return [{"city": c, "source": s, "detail": d, "built_at": b} for c, s, d, b in rows]


def street_near(city: City, p: LatLon, max_m: float) -> str | None:
    """Name of the closest named street to p within max_m (to tell same-named places apart)."""
    from .geo import haversine_m

    d = max_m / 111_320
    rows = con().execute("""SELECT name, (lat1 + lat2) / 2, (lon1 + lon2) / 2 FROM segments
                            WHERE city = ? AND name IS NOT NULL AND lat1 BETWEEN ? AND ? AND lon1 BETWEEN ? AND ?""",
                         [city.id, p[0] - 2 * d, p[0] + 2 * d, p[1] - 2.2 * d, p[1] + 2.2 * d]).fetchall()
    best = min(rows, key=lambda r: haversine_m(p, (r[1], r[2])), default=None)
    return best[0] if best and haversine_m(p, (best[1], best[2])) <= max_m else None
