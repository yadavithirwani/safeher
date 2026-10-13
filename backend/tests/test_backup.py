"""Offline Plan B coverage contracts on small directed walking graphs.

Only the AreaData shell is synthetic: POIs, hours parsing, spatial indexing and
walking geometry use the production helpers. No cached city data is required.
"""

from datetime import datetime, timezone
import json
import math
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import networkx as nx
import pytest

from safeher.backup import BackupCoverage, LIMIT_MIN, WALK_M_PER_MIN
from safeher.geo import EARTH_RADIUS_M, haversine_m
from safeher.pois import Poi, PointIndex

DEPARTURE = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def point(east_m, north_m=0):
    """Metre-scale equatorial coordinates; east-west lengths are exact."""
    return (math.degrees(north_m / EARTH_RADIUS_M),
            math.degrees(east_m / EARTH_RADIUS_M))


def area(coords, links=(), pois=(), tz=timezone.utc):
    graph = nx.MultiDiGraph()
    for node, (lat, lon) in coords.items():
        graph.add_node(node, y=lat, x=lon)
    for u, v, length in links:
        graph.add_edge(u, v, length=length)
    return SimpleNamespace(
        graph=graph, nodes=PointIndex((p, n) for n, p in coords.items()),
        pois=list(pois), area=SimpleNamespace(tz=tz),
    )


def place(osm, location, *, name=None, kind="cafe", hours="24/7", access=None):
    return Poi(osm, kind, *location, name=name or osm, opening_hours=hours,
               tags={} if access is None else {"access": access})


def route_edge(data, u, v, length=None):
    start = (data.graph.nodes[u]["y"], data.graph.nodes[u]["x"])
    end = (data.graph.nodes[v]["y"], data.graph.nodes[v]["x"])
    return SimpleNamespace(u=u, v=v,
                           length_m=length if length is not None else haversine_m(start, end),
                           points=[start, end])


@pytest.fixture
def single_street():
    return area({0: point(0), 1: point(100)}, [(0, 1, 100)])


def test_network_distance_not_straight_line():
    coords = {0: point(0), 1: point(50), 2: point(1000, 500)}
    cafe = place("n/cafe", coords[1])
    data = area(coords, [(0, 2, 450), (2, 1, 450)], [cafe])
    distances, nearest, eligible, assumed = BackupCoverage(data, DEPARTURE).distances(0)
    assert haversine_m(coords[0], cafe.latlon) == pytest.approx(50)
    assert distances[0] == pytest.approx(900)
    assert nearest[0] == (cafe, False)
    assert (eligible, assumed) == (1, 0)


def test_directed_reachability_is_toward_help():
    data = area({0: point(0), 1: point(200)}, [(1, 0, 200)], [place("n/cafe", point(200))])
    assert BackupCoverage(data, DEPARTURE).distances(0)[0] == {1: 0}


def test_reverse_dijkstra_matches_independent_forward_paths():
    coords = {n: point(n * 200) for n in range(7)}
    links = [(0, 1, 90), (0, 1, 200), (1, 2, 110), (2, 0, 50),
             (1, 3, 50), (3, 4, 80), (4, 2, 10), (2, 5, 1400), (6, 0, 1300)]
    pois = [place("n/a", point(400, 20)), place("n/b", point(800, 35))]
    data = area(coords, links, pois)
    distances, nearest, eligible, _ = BackupCoverage(data, DEPARTURE).distances(0)
    for node in coords:
        options = []
        for poi in pois:
            connector, destination = data.nodes.nearest(poi.latlon, 100)
            if nx.has_path(data.graph, node, destination):
                walking = nx.shortest_path_length(data.graph, node, destination, weight="length")
                options.append((walking + connector, poi))
        best = min(options, key=lambda item: item[0]) if options else None
        if best is None or best[0] > LIMIT_MIN * WALK_M_PER_MIN:
            assert node not in distances
        else:
            assert distances[node] == pytest.approx(best[0])
            assert nearest[node] == (best[1], False)
    assert eligible == 2
    assert distances[0] == pytest.approx(220)


@pytest.mark.parametrize("length, reachable", [(1199, True), (1200, True), (1201, False)])
def test_search_limit_boundary(length, reachable):
    data = area({0: point(0), 1: point(200)}, [(0, 1, length)], [place("n/cafe", point(200))])
    assert (0 in BackupCoverage(data, DEPARTURE).distances(0)[0]) is reachable


@pytest.mark.parametrize("hours", ["Mo-Su 13:00-23:00", "Mo-Su 11:00-12:10",
                                   "Mo-Su 11:00-12:16", "Mo-Su 11:00-12:05,12:06-23:00"])
def test_closed_soon_closing_or_split_hours_are_excluded(single_street, hours):
    single_street.pois = [place("n/cafe", point(100), hours=hours)]
    assert BackupCoverage(single_street, DEPARTURE).distances(0) == ({}, {}, 0, 0)


def test_elapsed_route_time_and_bucket_cache(single_street):
    single_street.pois = [place("n/cafe", point(100), hours="Mo-Su 11:00-12:20")]
    coverage = BackupCoverage(single_street, DEPARTURE)
    assert coverage.distances(0)[2] == 1
    assert coverage.distances(3.99)[2] == 1
    assert coverage.distances(4)[2] == 0
    assert coverage.distances(0)[2] == 1


def test_hours_use_area_timezone(single_street):
    single_street.area.tz = ZoneInfo("Asia/Kolkata")
    single_street.pois = [place("n/cafe", point(100), hours="Mo-Su 17:00-18:00")]
    assert BackupCoverage(single_street, DEPARTURE).distances(0)[2:] == (1, 0)


@pytest.mark.parametrize("hours", [None, "ask the owner"])
def test_missing_invalid_hours_are_explicit_assumptions(single_street, hours):
    single_street.pois = [place("n/cafe", point(100), hours=hours)]
    result = BackupCoverage(single_street, DEPARTURE).describe([route_edge(single_street, 0, 1)])
    assert result["eligible_places_min"] == 1
    assert result["assumed_hours_places_max"] == 1
    assert result["worst_help_point"]["hours_assumed"] is True
    assert "assumptions" in result["limitations"]
    assert BackupCoverage(single_street, DEPARTURE.replace(hour=3)).distances(0)[2] == 0


@pytest.mark.parametrize("access", ["no", "private", "customers", "permit", "members", "PRIVATE"])
def test_restricted_access_excluded(single_street, access):
    single_street.pois = [place("n/cafe", point(100), access=access)]
    assert BackupCoverage(single_street, DEPARTURE).distances(0) == ({}, {}, 0, 0)


def test_kind_snap_and_osm_deduplication(single_street):
    cafe = place("n/cafe", point(100))
    single_street.pois = [cafe, cafe, place("n/bus", point(100), kind="bus_stop"),
                          place("n/far", point(100, 101)), place("n/public", point(100), access="yes")]
    assert BackupCoverage(single_street, DEPARTURE).distances(0)[2] == 2


def test_connector_distance_included(single_street):
    single_street.pois = [place("n/cafe", point(100, 60))]
    result = BackupCoverage(single_street, DEPARTURE).describe([route_edge(single_street, 0, 1)])
    assert result["max_walk_min"] == pytest.approx(round(135 / WALK_M_PER_MIN, 1))
    assert result["within_threshold_share"] == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_midpoint_respects_street_direction(single_street, reverse):
    if reverse:
        single_street.graph.add_edge(1, 0, length=100)
    single_street.pois = [place("n/cafe", point(0))]
    result = BackupCoverage(single_street, DEPARTURE).describe([route_edge(single_street, 0, 1)])
    assert result["max_walk_min"] == (round(75 / WALK_M_PER_MIN, 1) if reverse else None)
    assert result["unavailable_length_m"] == (0 if reverse else 100)


@pytest.mark.parametrize("mode", ["no_pois", "disconnected", "outside_limit"])
def test_unavailable_metrics_null_not_zero(single_street, mode):
    if mode != "no_pois":
        single_street.graph.add_node(2, y=point(400)[0], x=point(400)[1])
        single_street.nodes = PointIndex([(point(0), 0), (point(100), 1), (point(400), 2)])
        single_street.pois = [place("n/cafe", point(400))]
        if mode == "outside_limit":
            single_street.graph.add_edge(1, 2, length=1201)
    result = BackupCoverage(single_street, DEPARTURE).describe([route_edge(single_street, 0, 1)])
    for key in ("max_walk_min", "max_known_walk_min", "worst_point", "worst_help_point"):
        assert result[key] is None
    assert result["within_threshold_share"] == 0
    assert result["unavailable_length_m"] == 100
    assert len(result["gaps"]) == 1
    assert result["gaps"][0]["unavailable"] is True
    assert result["gaps"][0]["length_m"] == 100


def test_partial_unknown_has_no_overall_maximum():
    coords = {0: point(0), 1: point(50), 2: point(100)}
    data = area(coords, [(0, 1, 50), (1, 2, 50)], [place("n/cafe", point(50))])
    result = BackupCoverage(data, DEPARTURE).describe([route_edge(data, 0, 1), route_edge(data, 1, 2)])
    assert result["max_walk_min"] is None
    assert result["max_known_walk_min"] == pytest.approx(round(25 / WALK_M_PER_MIN, 1))
    assert result["within_threshold_share"] == .5
    assert result["unavailable_length_m"] == 50


def test_empty_route_has_no_fabricated_distance():
    result = BackupCoverage(area({}), DEPARTURE).describe([])
    assert result["max_walk_min"] is None
    assert result["max_known_walk_min"] is None
    assert result["worst_help_point"] is None
    assert result["gaps"] == []


def test_gap_merges_across_edge_boundaries():
    coords = {0: point(0), 1: point(100), 2: point(200)}
    data = area(coords, [(0, 1, 100), (1, 2, 100)])
    result = BackupCoverage(data, DEPARTURE).describe([route_edge(data, 0, 1), route_edge(data, 1, 2)])
    assert len(result["gaps"]) == 1
    gap = result["gaps"][0]
    assert gap["start"] == coords[0]
    assert gap["end"] == coords[2]
    assert gap["length_m"] == 200
    assert gap["unavailable"] is True
    assert len(gap["geometry"]) == 5


def test_covered_stretch_splits_known_and_unavailable_gaps():
    coords = {n: point(n * 80) for n in range(5)}
    coords[5] = point(600)
    # First edge samples precede minute 1. The second destination then opens;
    # final edges cannot walk backwards to either destination.
    data = area(coords, [(0, 1, 80), (1, 2, 80), (2, 3, 80), (3, 4, 80), (1, 5, 500)],
                [place("n/a", coords[5]), place("n/b", coords[2], hours="Mo-Su 12:01-23:00")])
    edges = [route_edge(data, n, n + 1) for n in range(4)]
    result = BackupCoverage(data, DEPARTURE).describe(edges)
    assert len(result["gaps"]) == 2
    assert [g["length_m"] for g in result["gaps"]] == [80, 160]
    assert [g["unavailable"] for g in result["gaps"]] == [False, True]
    assert result["within_threshold_share"] == .25


def test_repeatable_json_safe_output(single_street):
    single_street.pois = [place("n/cafe", point(100))]
    coverage = BackupCoverage(single_street, DEPARTURE)
    edges = [route_edge(single_street, 0, 1)]
    first = coverage.describe(edges)
    coverage.distances(27)
    assert coverage.describe(edges) == first
    assert BackupCoverage(single_street, DEPARTURE).describe(edges) == first
    assert json.loads(json.dumps(first, allow_nan=False))["max_walk_min"] == first["max_walk_min"]


def test_equal_distance_tie_independent_of_poi_order(single_street):
    pois = [place("n/a", point(100), name="Alpha"), place("n/b", point(100), name="Beta")]
    single_street.pois = pois
    edges = [route_edge(single_street, 0, 1)]
    first = BackupCoverage(single_street, DEPARTURE).describe(edges)
    single_street.pois = list(reversed(pois))
    assert BackupCoverage(single_street, DEPARTURE).describe(edges) == first


def test_arrival_uses_network_length_not_geometry(single_street):
    # Cached/generalized geometry differs from graph length: arrival samples
    # should be at 200/80 and 600/80 minutes, not 25/80 and 75/80 minutes.
    single_street.graph[0][1][0]["length"] = 800
    single_street.pois = [place("n/cafe", point(100), hours="Mo-Su 11:00-12:20")]
    result = BackupCoverage(single_street, DEPARTURE).describe([route_edge(single_street, 0, 1, 800)])
    assert result["unavailable_length_m"] > 0
    assert result["max_walk_min"] is None
    assert result["eligible_places_min"] == 0
