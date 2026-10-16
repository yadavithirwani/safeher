"""Walking routes on the cached OSM graph: shortest, most-supported, and a distinct alternative."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from functools import lru_cache

import networkx as nx

from . import config
from .areas import AREAS, Area
from .geo import LatLon, haversine_m
from .viirs import ViirsGrid, load_for as load_viirs
from .pois import SAFE_SPACE_KINDS, STOP_KINDS, TRANSIT_KINDS, Poi, PointIndex, pois_from_osm

_R = config.scoring()["routing"]
MAX_SNAP_M = float(_R["max_snap_m"])  # origin/destination must be this close to a walkable street
MAX_DETOUR = float(_R["max_detour"])  # alternatives may be at most this many times the shortest length
MAX_OVERLAP = float(_R["max_overlap"])  # routes sharing more of their length than this are duplicates
SUPPORT_LAMBDAS = tuple(_R["support_lambdas"])  # how strongly "Most support" avoids weak streets
ALTERNATIVE_PENALTIES = tuple(_R["alternative_penalties"])  # cost multiplier on already-used streets


@dataclass
class Edge:
    u: int
    v: int
    length_m: float
    points: list[LatLon]  # oriented from u to v
    tags: dict


@dataclass
class AreaData:
    area: Area
    graph: nx.MultiDiGraph
    nodes: PointIndex[int]
    pois: list[Poi]
    lamps: PointIndex[Poi]
    transit: PointIndex[Poi]
    safe_spaces: PointIndex[Poi]
    stops: PointIndex[Poi]
    viirs: ViirsGrid | None = None

    def node_latlon(self, n: int) -> LatLon:
        d = self.graph.nodes[n]
        return (d["y"], d["x"])

    def snap(self, p: LatLon) -> tuple[int, float]:
        hit = self.nodes.nearest(p, MAX_SNAP_M)
        if hit is None:
            raise ValueError(f"no walkable street within {MAX_SNAP_M} m of {p}")
        d, n = hit
        return n, d


def _parse_listish(value):
    if isinstance(value, str) and value.startswith("["):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
    return value


@lru_cache(maxsize=4)
def load_area(area_id: str) -> AreaData:
    import osmnx as ox

    area = AREAS[area_id]
    graph_path = area.dir / "walk.graphml"
    if not graph_path.exists():
        raise FileNotFoundError(f"{area_id} not cached; run: python -m safeher.fetch {area_id}")
    graph = ox.load_graphml(graph_path)
    for _, _, d in graph.edges(data=True):
        for k in ("lit", "highway", "name", "sidewalk", "footway"):
            if k in d:
                d[k] = _parse_listish(d[k])
    pois = pois_from_osm(json.loads((area.dir / "pois.json").read_text()))

    def index(kinds: set[str]) -> PointIndex[Poi]:
        return PointIndex((p.latlon, p) for p in pois if p.kind in kinds)

    return AreaData(
        area=area,
        graph=graph,
        nodes=PointIndex(((d["y"], d["x"]), n) for n, d in graph.nodes(data=True)),
        pois=pois,
        lamps=index({"street_lamp"}),
        transit=index(TRANSIT_KINDS),
        safe_spaces=index(SAFE_SPACE_KINDS),
        stops=index(STOP_KINDS),
        viirs=load_viirs(area),
    )


def edge_between(data: AreaData, u: int, v: int, weight: str | None = None) -> Edge:
    """The edge u→v (the cheapest one if parallel edges exist), with geometry oriented u→v."""
    options = data.graph.get_edge_data(u, v)
    key = min(options, key=lambda k: options[k].get(weight or "length", options[k]["length"]))
    d = options[key]
    start, end = data.node_latlon(u), data.node_latlon(v)
    if "geometry" in d:
        pts = [(y, x) for x, y in d["geometry"].coords]
        if haversine_m(pts[0], start) > haversine_m(pts[-1], start):
            pts.reverse()
    else:
        pts = [start, end]
    tags = {k: d[k] for k in ("highway", "lit", "name", "sidewalk", "footway") if k in d}
    return Edge(u, v, float(d["length"]), pts, tags)


def path_edges(data: AreaData, nodes: list[int], weight: str | None = None) -> list[Edge]:
    return [edge_between(data, u, v, weight) for u, v in zip(nodes, nodes[1:])]


def path_length(data: AreaData, nodes: list[int]) -> float:
    return sum(e.length_m for e in path_edges(data, nodes))


def overlap(data: AreaData, a: list[int], b: list[int]) -> float:
    """Share of a's length that also appears in b (undirected edges)."""
    b_edges = {frozenset(p) for p in zip(b, b[1:])}
    total = shared = 0.0
    for e in path_edges(data, a):
        total += e.length_m
        if frozenset((e.u, e.v)) in b_edges:
            shared += e.length_m
    return shared / total if total else 1.0


def _shortest(graph: nx.MultiDiGraph, s: int, t: int, cost) -> list[int] | None:
    try:
        return nx.shortest_path(graph, s, t, weight=cost)
    except nx.NetworkXNoPath:
        return None


def candidate_paths(data: AreaData, origin: LatLon, dest: LatLon, edge_support: dict | None = None,
                    rank=None) -> list[tuple[str, list[int]]]:
    """Up to three distinct walking paths: shortest, most-supported (if edge_support given), alternative.

    edge_support maps frozenset({u, v}) → 0-100 support score for that street.
    rank(path) → number: if given, every support setting is tried and the highest-ranked valid path
    becomes "Most support" (the planner ranks by the full route score, which the per-street search
    cost can't capture: it also weighs the weakest stretch). Without it, the first valid path wins.
    """
    s, _ = data.snap(origin)
    t, _ = data.snap(dest)
    if s == t:
        raise ValueError("origin and destination are the same place")
    G = data.graph

    def by_length(u, v, d):
        return min(x["length"] for x in d.values())

    shortest = _shortest(G, s, t, by_length)
    if shortest is None:
        raise ValueError("no walking connection between these points")
    base_len = path_length(data, shortest)
    found: list[tuple[str, list[int]]] = [("Shortest", shortest)]

    def valid(path: list[int] | None) -> bool:
        if path is None or path_length(data, path) > MAX_DETOUR * base_len:
            return False
        return not any(overlap(data, path, p) > MAX_OVERLAP for _, p in found)

    def accept(label: str, path: list[int] | None) -> bool:
        if not valid(path):
            return False
        found.append((label, path))
        return True

    if edge_support:
        # Prefer better-supported streets: a street scoring 0 "costs" up to (1 + λ)× its length.
        options: list[list[int]] = []
        for lam in SUPPORT_LAMBDAS:
            def by_support(u, v, d, lam=lam):
                length = min(x["length"] for x in d.values())
                return length * (1 + lam * (1 - edge_support.get(frozenset((u, v)), 50.0) / 100))
            path = _shortest(G, s, t, by_support)
            if valid(path) and path not in options:
                options.append(path)
                if rank is None:
                    break
        if options:
            found.append(("Most support", max(options, key=rank) if rank else options[0]))

    # Penalise streets already used, to find a genuinely different option.
    used = {frozenset(p) for _, path in found for p in zip(path, path[1:])}
    for penalty in ALTERNATIVE_PENALTIES:
        def by_penalised(u, v, d, penalty=penalty):
            length = min(x["length"] for x in d.values())
            return length * (penalty if frozenset((u, v)) in used else 1.0)
        if accept("Alternative", _shortest(G, s, t, by_penalised)):
            break
    return found
