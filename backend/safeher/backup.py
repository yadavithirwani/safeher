"""Plan B access estimates on the directed walking graph, separate from safety scoring.

Parameters come from config/scoring.json ("plan_b"). Results are identical to the original
implementation (tests/reference/backup_v1.py, checked in tests/test_backup_equivalence.py); this
version avoids repeated work:
  - each place's opening status is computed once per trip minute, not 17 times per bucket;
  - the multi-source search runs once per distinct set of open places (sets change only when a
    place opens or closes), cached across requests for the same graph;
  - reverse adjacency with the shortest parallel edge is built once per graph.
"""
from __future__ import annotations

import heapq
import math
import weakref
from collections import OrderedDict
from datetime import timedelta
from itertools import count

from . import config
from .geo import haversine_m, interpolate, split_polyline
from .pois import SAFE_SPACE_KINDS

_P = config.scoring()["plan_b"]
VERSION = str(_P["version"])
WALK_M_PER_MIN = float(config.scoring()["routing"]["walk_m_per_min"])
SAMPLE_M = float(_P["sample_m"])
POI_SNAP_M = float(_P["poi_snap_m"])
LIMIT_MIN = int(_P["search_limit_min"])
GAP_MIN = int(_P["gap_threshold_min"])
PRIVATE_ACCESS = frozenset(_P["private_access_values"])
_SEARCH_CACHE_MAX = int(_P["search_cache_size"])

_REVERSE: "weakref.WeakKeyDictionary[object, dict]" = weakref.WeakKeyDictionary()
_SEARCHES: "weakref.WeakKeyDictionary[object, OrderedDict]" = weakref.WeakKeyDictionary()


def _reverse_adjacency(graph) -> dict:
    """node → [(predecessor, shortest parallel edge length)], built once per graph object."""
    rev = _REVERSE.get(graph)
    if rev is None:
        rev = {n: [(p, min(float(e["length"]) for e in opts.values())) for p, opts in graph.pred[n].items()]
               for n in graph.nodes}
        _REVERSE[graph] = rev
    return rev


def _multi_source(graph, sources: tuple) -> tuple[dict, dict]:
    """Reversed multi-source Dijkstra from (osm, node, connector) sources; cached per graph + source set.

    Returns (distances, nearest_source_index) where the index points into `sources`.
    """
    cache = _SEARCHES.setdefault(graph, OrderedDict())
    if sources in cache:
        cache.move_to_end(sources)
        return cache[sources]
    distances, nearest = {}, {}
    heap, sequence = [], count()
    for i, (_, node, connector) in enumerate(sources):  # sources are sorted by osm id, like v1
        if connector < distances.get(node, math.inf):
            distances[node] = connector
            nearest[node] = i
            heapq.heappush(heap, (connector, next(sequence), node))
    rev = _reverse_adjacency(graph)
    limit = LIMIT_MIN * WALK_M_PER_MIN
    while heap:
        distance, _, node = heapq.heappop(heap)
        if distance != distances[node]:
            continue
        for predecessor, length in rev.get(node, ()):
            candidate = distance + length
            if candidate <= limit and candidate < distances.get(predecessor, math.inf):
                distances[predecessor] = candidate
                nearest[predecessor] = nearest[node]
                heapq.heappush(heap, (candidate, next(sequence), predecessor))
    cache[sources] = (distances, nearest)
    if len(cache) > _SEARCH_CACHE_MAX:
        cache.popitem(last=False)
    return distances, nearest


class BackupCoverage:
    """One request-local index shared by candidate routes; no stale global hours cache.

    POI-to-node connectors are straight-line approximations, not verified entrances.
    Distances over the mapped street network respect directed edges. A reversed
    multi-source Dijkstra computes distances *to* destinations, including connectors.
    """

    def __init__(self, data, departure):
        self.data = data
        self.departure = departure
        self.places = []
        seen = set()
        for poi in data.pois:
            if poi.kind not in SAFE_SPACE_KINDS or poi.osm in seen:
                continue
            if str(poi.tags.get("access", "")).lower() in PRIVATE_ACCESS:
                continue
            seen.add(poi.osm)
            hit = data.nodes.nearest(poi.latlon, POI_SNAP_M)
            if hit:
                connector, node = hit
                self.places.append((poi, node, connector))
        self.places.sort(key=lambda item: item[0].osm)
        self._open: dict[int, list[tuple[bool, bool]]] = {}  # minute → per-place (open, assumed)
        self.cache = {}

    def _status(self, minute: int) -> list[tuple[bool, bool]]:
        if minute not in self._open:
            local = (self.departure + timedelta(minutes=minute)).astimezone(self.data.area.tz)
            self._open[minute] = [poi.is_open(local) for poi, _, _ in self.places]
        return self._open[minute]

    def distances(self, elapsed_min):
        # Minute buckets: require a place open throughout the possible arrival window.
        minute = math.floor(elapsed_min)
        if minute in self.cache:
            return self.cache[minute]
        window = [self._status(minute + i) for i in range(LIMIT_MIN + 2)]  # minutes m … m+16 inclusive
        chosen = [i for i in range(len(self.places)) if all(w[i][0] for w in window)]
        assumed = {i: any(w[i][1] for w in window) for i in chosen}
        sources = tuple((self.places[i][0].osm, self.places[i][1], self.places[i][2]) for i in chosen)
        dist, nearest_idx = _multi_source(self.data.graph, sources)
        nearest = {node: (self.places[chosen[k]][0], assumed[chosen[k]]) for node, k in nearest_idx.items()}
        result = (dist, nearest, len(chosen), sum(assumed.values()))
        self.cache[minute] = result
        return result

    def describe(self, edges):
        samples, along, network_along = [], 0.0, 0.0
        for edge in edges:
            segments = split_polyline(edge.points, SAMPLE_M)
            geometric = sum(haversine_m(a, b) for a, b in zip(edge.points, edge.points[1:]))
            progressed = 0.0
            for points in segments:
                length = sum(haversine_m(a, b) for a, b in zip(points, points[1:]))
                fraction = (progressed + length / 2) / geometric if geometric else 0
                elapsed = (network_along + fraction * edge.length_m) / WALK_M_PER_MIN
                distances, nearest, eligible, assumed = self.distances(elapsed)
                options = [(edge.v, (1 - fraction) * edge.length_m)]
                if self.data.graph.has_edge(edge.v, edge.u):
                    reverse_length = min(float(e["length"]) for e in
                                         self.data.graph[edge.v][edge.u].values())
                    options.append((edge.u, fraction * reverse_length))
                reachable = [(distances[node] + offset, nearest[node]) for node, offset in options
                             if node in distances and distances[node] + offset <= LIMIT_MIN * WALK_M_PER_MIN]
                best = min(reachable, key=lambda item: item[0]) if reachable else None
                samples.append({"start": points[0], "end": points[-1],
                    "point": interpolate(points[0], points[-1], .5), "length": length,
                    "along": along + length / 2, "distance": best[0] if best else None,
                    "place": best[1] if best else None, "eligible": eligible, "assumed": assumed})
                progressed += length
                along += length
            network_along += edge.length_m
        gaps, run = [], []

        def flush():
            if run:
                gaps.append({"start": run[0]["start"], "end": run[-1]["end"],
                    "length_m": round(sum(s["length"] for s in run)),
                    "geometry": [run[0]["start"]] + [s["end"] for s in run],
                    "unavailable": any(s["distance"] is None for s in run)})
                run.clear()

        for sample in samples:
            if sample["distance"] is None or sample["distance"] > GAP_MIN * WALK_M_PER_MIN:
                run.append(sample)
            else:
                flush()
        flush()
        known = [s for s in samples if s["distance"] is not None]
        worst = max(known, key=lambda s: s["distance"]) if known else None
        unknown_m = sum(s["length"] for s in samples if s["distance"] is None)
        covered = sum(s["length"] for s in known if s["distance"] <= GAP_MIN * WALK_M_PER_MIN)
        return {"version": VERSION, "threshold_min": GAP_MIN, "search_limit_min": LIMIT_MIN,
            "sample_spacing_m": SAMPLE_M, "walking_speed_m_per_min": WALK_M_PER_MIN,
            "within_threshold_share": round(covered / along, 3) if along else 0,
            "unavailable_length_m": round(unknown_m),
            "max_walk_min": round(worst["distance"] / WALK_M_PER_MIN, 1) if worst and not unknown_m else None,
            "max_known_walk_min": round(worst["distance"] / WALK_M_PER_MIN, 1) if worst else None,
            "worst_point": worst["point"] if worst else None,
            "worst_help_point": None if not worst else {
                "name": worst["place"][0].name, "kind": worst["place"][0].kind,
                "lat": worst["place"][0].lat, "lon": worst["place"][0].lon,
                "hours_assumed": worst["place"][1]},
            "eligible_places_min": min((s["eligible"] for s in samples), default=0),
            "assumed_hours_places_max": max((s["assumed"] for s in samples), default=0),
            "gaps": gaps,
            "limitations": _P["limitations"].format(snap=f"{POI_SNAP_M:.0f}", window=LIMIT_MIN + 1,
                                                     limit=LIMIT_MIN)}
