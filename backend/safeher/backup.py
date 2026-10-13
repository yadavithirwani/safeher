"""Plan B access estimates on the directed walking graph, separate from safety scoring."""
from __future__ import annotations

import heapq
import math
from datetime import timedelta
from itertools import count

from .geo import haversine_m, interpolate, split_polyline
from .pois import SAFE_SPACE_KINDS

WALK_M_PER_MIN = 80.0
SAMPLE_M = 50.0
POI_SNAP_M = 100.0
LIMIT_MIN = 15
GAP_MIN = 5
PRIVATE_ACCESS = {"no", "private", "customers", "permit", "members"}


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
        self.cache = {}

    def distances(self, elapsed_min):
        # Minute buckets: require a place open throughout the possible arrival window.
        minute = math.floor(elapsed_min)
        if minute in self.cache:
            return self.cache[minute]
        local = (self.departure + timedelta(minutes=minute)).astimezone(self.data.area.tz)
        end = (self.departure + timedelta(minutes=minute + LIMIT_MIN + 1)).astimezone(self.data.area.tz)
        candidates = []
        for poi, node, connector in self.places:
            # Check each minute, not just endpoints (split opening intervals exist).
            times = [local + timedelta(minutes=i) for i in range(LIMIT_MIN + 2)]
            readings = [poi.is_open(t) for t in times]
            if all(opened for opened, _ in readings) and poi.is_open(end)[0]:
                candidates.append((poi, node, connector, any(assumed for _, assumed in readings)))
        distances, nearest = {}, {}
        heap, sequence = [], count()
        for poi, node, connector, assumed in sorted(candidates, key=lambda item: item[0].osm):
            if connector < distances.get(node, math.inf):
                distances[node] = connector
                nearest[node] = (poi, assumed)
                heapq.heappush(heap, (connector, next(sequence), node))
        graph = self.data.graph
        limit = LIMIT_MIN * WALK_M_PER_MIN
        while heap:
            distance, _, node = heapq.heappop(heap)
            if distance != distances[node]:
                continue
            for predecessor, options in graph.pred[node].items():
                length = min(float(edge["length"]) for edge in options.values())
                candidate = distance + length
                if candidate <= limit and candidate < distances.get(predecessor, math.inf):
                    distances[predecessor] = candidate
                    nearest[predecessor] = nearest[node]
                    heapq.heappush(heap, (candidate, next(sequence), predecessor))
        result = (distances, nearest, len(candidates), sum(c[3] for c in candidates))
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
        return {"version": "1", "threshold_min": GAP_MIN, "search_limit_min": LIMIT_MIN,
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
            "limitations": "Estimated from sampled route stretches and cached walking streets, not verified entrances. "
                "POIs connect to a street node within 100 m using a straight-line estimate. "
                "Hours must indicate open throughout a 16-minute window at estimated route arrival; "
                "missing or invalid hours use typical assumptions. No mapped option within 15 minutes "
                "does not mean no help exists. Businesses are not verified refuges; access and assistance are not guaranteed. "
                "This metric does not change the route score or ranking."}
