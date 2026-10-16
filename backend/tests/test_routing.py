from datetime import datetime, time

import networkx as nx
import pytest

from safeher.cities import Area
from safeher.geo import haversine_m
from safeher.pois import Poi, PointIndex, classify
from safeher.routing import AreaData, candidate_paths, edge_between, overlap, path_length

START, END = (12.970, 77.600), (12.970, 77.610)


def make_area(bottom_lat: float = 12.9688) -> AreaData:
    """Two streets from START to END: a dark top street and a (longer) lit bottom street."""
    coords = {0: START, 1: (12.971, 77.605), 2: END, 3: (bottom_lat, 77.605)}
    g = nx.MultiDiGraph()
    for n, (lat, lon) in coords.items():
        g.add_node(n, y=lat, x=lon)
    for u, v, lit in [(0, 1, "no"), (1, 2, "no"), (0, 3, "yes"), (3, 2, "yes")]:
        length = haversine_m(coords[u], coords[v])
        for a, b in ((u, v), (v, u)):
            g.add_edge(a, b, length=length, lit=lit, highway="residential")
    empty: PointIndex[Poi] = PointIndex(())
    return AreaData(
        area=Area("test", "Test", "Test", 12.9, 77.5, 13.0, 77.7, timezone="UTC"),
        graph=g, nodes=PointIndex((p, n) for n, p in coords.items()),
        pois=[], lamps=empty, transit=empty, safe_spaces=empty, stops=empty,
    )


def support_from_lit(data: AreaData) -> dict:
    return {frozenset((u, v)): (100.0 if d["lit"] == "yes" else 0.0) for u, v, d in data.graph.edges(data=True)}


def test_shortest_and_most_supported_differ():
    data = make_area()
    routes = dict(candidate_paths(data, START, END, support_from_lit(data)))
    assert routes["Shortest"] == [0, 1, 2]
    assert routes["Most support"] == [0, 3, 2]


def test_most_supported_rejected_when_detour_too_long():
    data = make_area(bottom_lat=12.962)  # bottom street now far more than 1.5x longer
    routes = dict(candidate_paths(data, START, END, support_from_lit(data)))
    assert "Most support" not in routes
    assert all(path_length(data, p) <= 1.5 * path_length(data, routes["Shortest"]) for p in routes.values())


def test_routes_are_distinct():
    data = make_area()
    paths = [p for _, p in candidate_paths(data, START, END, support_from_lit(data))]
    for i, a in enumerate(paths):
        for b in paths[i + 1:]:
            assert overlap(data, a, b) <= 0.8


def test_far_away_origin_rejected():
    with pytest.raises(ValueError, match="no walkable street"):
        candidate_paths(make_area(), (12.99, 77.60), END)


def test_same_origin_and_destination_rejected():
    with pytest.raises(ValueError, match="same place"):
        candidate_paths(make_area(), START, START)


def test_edge_geometry_is_oriented_from_u_to_v():
    from shapely.geometry import LineString

    data = make_area()
    # Store geometry backwards (v→u) on the 0→1 edge; edge_between must flip it.
    d = data.graph.get_edge_data(0, 1)[0]
    d["geometry"] = LineString([(77.605, 12.971), (77.6025, 12.9706), (77.600, 12.970)])
    e = edge_between(data, 0, 1)
    assert e.points[0] == START and e.points[-1] == (12.971, 77.605)




# --- POIs ---------------------------------------------------------------------------------


@pytest.mark.parametrize("tags, kind", [
    ({"amenity": "police"}, "police"),
    ({"office": "government", "government": "police"}, "police"),
    ({"amenity": "fast_food"}, "restaurant"),
    ({"shop": "chemist"}, "pharmacy"),
    ({"railway": "subway_entrance"}, "metro"),
    ({"highway": "street_lamp"}, "street_lamp"),
    ({"amenity": "bank"}, None),
])
def test_classify(tags, kind):
    assert classify(tags) == kind


def test_opening_hours_from_osm_tag():
    shop = Poi("n/1", "pharmacy", 0, 0, opening_hours="Mo-Su 08:00-20:00")
    assert shop.is_open(datetime(2026, 10, 7, 19, 0)) == (True, False)
    assert shop.is_open(datetime(2026, 10, 7, 21, 0)) == (False, False)


def test_typical_hours_used_and_flagged_when_tag_missing_or_broken():
    cafe = Poi("n/2", "cafe", 0, 0)
    assert cafe.is_open(datetime(2026, 10, 7, 22, 0)) == (True, True)
    assert cafe.is_open(datetime(2026, 10, 7, 23, 30)) == (False, True)
    broken = Poi("n/3", "cafe", 0, 0, opening_hours="ask the owner")
    assert broken.is_open(datetime(2026, 10, 7, 22, 0)) == (True, True)


def test_police_and_hospitals_assumed_24_7():
    for kind in ("police", "hospital"):
        assert Poi("n/4", kind, 0, 0).is_open(datetime(2026, 10, 7, 3, 0)) == (True, True)


def test_point_index_radius_and_order():
    idx = PointIndex([((12.9700, 77.6000), "a"), ((12.9705, 77.6000), "b"), ((12.9800, 77.6000), "far")])
    hits = idx.within((12.9701, 77.6000), 100)
    assert [item for _, item in hits] == ["a", "b"]
    assert idx.nearest((12.9701, 77.6000), 20)[1] == "a"
    assert idx.nearest((12.9701, 77.6000), 5) is None  # "a" is ~11 m away
    assert idx.nearest((13.5, 77.6), 500) is None
