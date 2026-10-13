"""Stress-test every dataset the app relies on. Each check prints what it measured, so the
report doubles as a data-quality summary (run with -s)."""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import time

import networkx as nx
import pytest

from safeher import config, ncrb
from safeher.areas import AREAS
from safeher.geo import haversine_m, polyline_length_m
from safeher.pois import TYPICAL_HOURS, classify
from safeher.routing import MAX_SNAP_M, load_area
from safeher.signals import SIGNALS

AREA_IDS = sorted(AREAS)
needs_data = pytest.mark.skipif(
    not all((AREAS[a].dir / "walk.graphml").exists() for a in AREA_IDS), reason="OSM snapshots not fetched")
KNOWN_HIGHWAYS = {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary",
                  "tertiary_link", "residential", "living_street", "unclassified", "service", "footway",
                  "pedestrian", "path", "track", "steps", "cycleway", "corridor", "bridleway", "elevator",
                  "road", "busway", "motorway_link"}
KNOWN_LIT = {"yes", "no", "24/7", "automatic", "sunset-sunrise", "limited", "interval", "disused"}


def report(name, **numbers):
    print(f"\n[data] {name}: " + ", ".join(f"{k}={v}" for k, v in numbers.items()))


# --- Street graph ---------------------------------------------------------------------------


@needs_data
@pytest.mark.parametrize("area_id", AREA_IDS)
def test_graph_nodes_have_valid_coordinates_inside_area(area_id):
    data = load_area(area_id)
    a = data.area
    bad = [n for n, d in data.graph.nodes(data=True)
           if not (math.isfinite(d["y"]) and math.isfinite(d["x"]))]
    outside = [n for n, d in data.graph.nodes(data=True)
               if not (a.south - 0.01 <= d["y"] <= a.north + 0.01 and a.west - 0.01 <= d["x"] <= a.east + 0.01)]
    report(f"{area_id} nodes", total=data.graph.number_of_nodes(), non_finite=len(bad), outside_bbox=len(outside))
    assert not bad and not outside


@needs_data
@pytest.mark.parametrize("area_id", AREA_IDS)
def test_edge_lengths_match_their_geometry(area_id):
    data = load_area(area_id)
    ratios, nonpositive = [], 0
    for u, v, k, d in data.graph.edges(keys=True, data=True):
        if d["length"] <= 0:
            nonpositive += 1
            continue
        # Each edge's own geometry (parallel edges between two corners are different streets).
        pts = [(y, x) for x, y in d["geometry"].coords] if "geometry" in d else [data.node_latlon(u), data.node_latlon(v)]
        geom = polyline_length_m(pts) if u != v else d["length"]
        ratios.append(geom / d["length"])
    ratios.sort()
    off = sum(1 for r in ratios if abs(r - 1) > 0.02)
    report(f"{area_id} edge lengths", edges=len(ratios), nonpositive=nonpositive,
           off_by_over_2pct=off, worst=f"{ratios[0]:.3f}..{ratios[-1]:.3f}")
    assert nonpositive == 0
    assert off / len(ratios) < 0.001  # stored length agrees with drawn geometry


@needs_data
@pytest.mark.parametrize("area_id", AREA_IDS)
def test_graph_is_one_walkable_network(area_id):
    g = load_area(area_id).graph
    largest = max(nx.strongly_connected_components(g), key=len)
    share = len(largest) / g.number_of_nodes()
    one_way = sum(1 for u, v in g.edges() if not g.has_edge(v, u))
    report(f"{area_id} connectivity", largest_component_share=f"{share:.4f}", one_way_edges=one_way)
    assert share > 0.98  # nearly every street can reach every other on foot


@needs_data
@pytest.mark.parametrize("area_id", AREA_IDS)
def test_street_tags_use_known_values(area_id):
    data = load_area(area_id)
    hw, lit = Counter(), Counter()
    for _, _, d in data.graph.edges(data=True):
        for v in (d.get("highway") if isinstance(d.get("highway"), list) else [d.get("highway")]):
            hw[v] += 1
        if "lit" in d:
            for v in (d["lit"] if isinstance(d["lit"], list) else [d["lit"]]):
                lit[str(v)] += 1
    unknown_hw = {k: c for k, c in hw.items() if k not in KNOWN_HIGHWAYS}
    unknown_lit = {k: c for k, c in lit.items() if k not in KNOWN_LIT}
    report(f"{area_id} tags", highway_kinds=len(hw), unknown_highway=unknown_hw, lit_values=dict(lit),
           unknown_lit=unknown_lit)
    assert sum(unknown_hw.values()) / sum(hw.values()) < 0.001
    assert not unknown_lit


# --- Places ---------------------------------------------------------------------------------


@needs_data
@pytest.mark.parametrize("area_id", AREA_IDS)
def test_places_are_valid_unique_and_inside_area(area_id):
    a = AREAS[area_id]
    raw = json.loads((a.dir / "pois.json").read_text())
    ids = Counter(p["osm"] for p in raw)
    dupes = [k for k, c in ids.items() if c > 1]
    outside = [p["osm"] for p in raw if not a.contains(p["lat"], p["lon"])]
    blank_names = [p["osm"] for p in raw if "name" in p["tags"] and not str(p["tags"]["name"]).strip()]
    report(f"{area_id} places", total=len(raw), duplicate_ids=len(dupes), outside_bbox=len(outside),
           blank_names=len(blank_names))
    assert not dupes and not blank_names
    assert len(outside) / len(raw) < 0.01  # Overpass "center" of a large outline may fall just outside


@needs_data
@pytest.mark.parametrize("area_id", AREA_IDS)
def test_every_quick_action_has_places(area_id):
    data = load_area(area_id)
    kinds = Counter(p.kind for p in data.pois)
    report(f"{area_id} quick-action coverage", **{k: kinds[k] for k in ("police", "toilets", "hospital", "pharmacy",
                                                                        "bus_stop", "metro", "street_lamp")})
    for k in ("police", "toilets", "hospital", "pharmacy"):
        assert kinds[k] > 0, f"no {k} mapped in {area_id}"
    assert kinds["bus_stop"] + kinds["metro"] + kinds["rail"] > 20


@needs_data
@pytest.mark.parametrize("area_id", AREA_IDS)
def test_opening_hours_parse_or_fall_back_safely(area_id):
    from datetime import datetime
    data = load_area(area_id)
    tagged = [p for p in data.pois if p.opening_hours]
    parsed = [p for p in tagged if p._parsed_hours is not None]
    when = datetime(2026, 10, 14, 21, 30)
    for p in data.pois:  # never raises, always returns (bool, bool)
        is_open, assumed = p.is_open(when)
        assert isinstance(is_open, bool) and isinstance(assumed, bool)
        assert assumed == (p._parsed_hours is None)
    report(f"{area_id} opening hours", with_tag=len(tagged), parsed=len(parsed),
           unparseable=len(tagged) - len(parsed))
    assert len(parsed) >= 0.8 * len(tagged)


@needs_data
@pytest.mark.parametrize("area_id", AREA_IDS)
def test_police_and_hospitals_are_reachable_from_the_network(area_id):
    data = load_area(area_id)
    far = [p for p in data.pois if p.kind in ("police", "hospital") and data.nodes.nearest(p.latlon, MAX_SNAP_M) is None]
    report(f"{area_id} reachable help points", far_from_streets=[p.name for p in far])
    assert not far


# --- Landmarks and default trips ---------------------------------------------------------


@needs_data
@pytest.mark.parametrize("area_id", AREA_IDS)
def test_landmarks_snap_to_streets_and_default_trip_plans(area_id):
    from datetime import datetime
    from safeher.plan import plan
    a = AREAS[area_id]
    data = load_area(area_id)
    snaps = {lm.name: round(data.nodes.nearest((lm.lat, lm.lon), MAX_SNAP_M)[0]) for lm in a.landmarks}
    report(f"{area_id} landmark snap distances (m)", **snaps)
    assert max(snaps.values()) < 150
    o, d = (next(l for l in a.landmarks if l.name == n) for n in a.default_trip)
    p = plan((o.lat, o.lon), (d.lat, d.lon), datetime(2026, 10, 14, 21, 30, tzinfo=a.tz))
    assert len(p["routes"]) >= 2


# --- Incidents (NCRB) -------------------------------------------------------------------------


@pytest.mark.skipif(not ncrb.OUT_JSON.exists(), reason="NCRB table not built")
def test_ncrb_table_is_consistent():
    t = json.loads(ncrb.OUT_JSON.read_text())
    d = t["districts"]
    avgs = [v["avg_cases_per_year"] for v in d.values()]
    report("NCRB", districts=len(d), min=min(avgs), max=max(avgs), years=t["years"])
    assert len(d) == 15 and all(a >= 0 for a in avgs)
    assert all(0 <= v["city_percentile"] <= 1 for v in d.values())
    assert 0 < t["confidence"] <= 0.2
    for a in AREAS.values():
        assert a.ncrb_district is None or a.ncrb_district in d


@pytest.mark.skipif(not ncrb.RAW_CSV.exists(), reason="raw NCRB CSV not downloaded")
def test_ncrb_table_matches_raw_csv():
    """Rebuilding from the raw CSV gives exactly the committed table."""
    committed = json.loads(ncrb.OUT_JSON.read_text())
    rebuilt = ncrb.build()
    assert rebuilt["districts"] == committed["districts"]


# --- Config -------------------------------------------------------------------------------------


def test_scoring_config_is_internally_consistent():
    c = config.scoring()
    for name in ("night", "day"):
        assert set(c["weights"][name]) == set(SIGNALS)
        assert all(0 <= w <= 1 for w in c["weights"][name].values())
    mins = [b["min"] for b in c["bands"]]
    assert sorted(mins, reverse=True) == mins and mins[-1] == 0
    assert c["route"]["mean_share"] + c["route"]["worst_share"] == pytest.approx(1)
    assert 0 < c["route"]["worst_percentile"] < 0.5
    l = c["lighting"]
    assert l["road_type_confidence"] < l["viirs_confidence"] < l["lamp_confidence"] < l["tag_confidence"] <= 1
    t = c["transit"]
    assert t["full_score_within_m"] < t["zero_score_beyond_m"] <= t["search_radius_m"]
    r = c["reports"]
    assert all(v != 0 for v in r["severity"].values())
    assert set(r["verification_factor"]) >= {"pending", "corroborated"}
    for kind, hours in TYPICAL_HOURS.items():
        assert hours is None or all(isinstance(h, time) for h in hours)
    assert set(c["safe_spaces"]["strong_kinds"]) <= set(c["safe_spaces"]["kinds"])
    ro = c["routing"]
    assert ro["max_detour"] > 1 and 0 < ro["max_overlap"] < 1 and ro["support_lambdas"] == sorted(ro["support_lambdas"], reverse=True)


def test_app_config_is_internally_consistent():
    a = config.app()
    assert a["default_area"] in AREAS
    if a["default_departure"]:
        h, m = map(int, a["default_departure"].split(":"))
        assert 0 <= h < 24 and 0 <= m < 60
    assert a["helplines"][0]["number"] == "112"
    assert set(a["google"]["nearby_types"]) == {"police", "toilets", "hospital", "pharmacy", "open_places"}
    assert all(isinstance(v, bool) for v in a["google"]["features"].values())
    assert "en" in a["languages"]


def test_osm_categories_classify_consistently():
    assert classify({"amenity": "police"}) == "police"
    assert classify({"amenity": "toilets"}) == "toilets"
    assert classify({"highway": "street_lamp"}) == "street_lamp"
