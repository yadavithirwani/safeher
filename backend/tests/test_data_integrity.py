"""Stress-test every dataset the app relies on. Each check prints what it measured, so the
report doubles as a data-quality summary (run with -s).

Two levels: the whole build (data/safeher.duckdb, every row, checked with SQL) and the in-memory
walking graph built from it for each city's default trip.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import time

import pytest

from safeher import config, store
from safeher.cities import CITIES, corridor
from safeher.geo import polyline_length_m
from safeher.pois import TYPICAL_HOURS, classify
from safeher.routing import MAX_SNAP_M
from safeher.signals import SIGNALS

CITY_IDS = sorted(CITIES)
needs_data = pytest.mark.skipif(not store.available(), reason="run: python -m safeher.data build")
KNOWN_HIGHWAYS = {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary",
                  "tertiary_link", "residential", "living_street", "unclassified", "service", "footway",
                  "pedestrian", "path", "track", "steps", "cycleway", "corridor", "bridleway", "elevator",
                  "road", "busway", "motorway_link"}
KNOWN_LIT = {"yes", "no", "24/7", "automatic", "sunset-sunrise", "limited", "interval", "disused"}


def report(name, **numbers):
    print(f"\n[data] {name}: " + ", ".join(f"{k}={v}" for k, v in numbers.items()))


def q(sql, *args):
    return store.con().execute(sql, list(args)).fetchone()


def default_corridor(city_id):
    c = CITIES[city_id]
    o, d = (next(lm for lm in c.landmarks if lm.name == n) for n in c.default_trip)
    return c, o, d, store.corridor_data(corridor((o.lat, o.lon), (d.lat, d.lon)))


# --- The build ---------------------------------------------------------------------------------


@needs_data
def test_database_has_exactly_the_configured_cities_and_sources():
    built = {r[0] for r in store.con().execute("SELECT DISTINCT city FROM segments").fetchall()}
    assert built == set(CITIES)
    src = Counter(r["city"] for r in store.sources())
    report("sources", **{c: n for c, n in src.items()})
    assert all(src[c] >= 3 for c in CITIES)  # OpenStreetMap, NASA night lights, NCRB (or "none")


@needs_data
@pytest.mark.parametrize("city_id", CITY_IDS)
def test_segments_have_valid_coordinates_and_lengths(city_id):
    c = CITIES[city_id]
    n, bad, nonpos = q("""SELECT count(*),
            count(*) FILTER (WHERE NOT (isfinite(lat1) AND isfinite(lon1) AND isfinite(lat2) AND isfinite(lon2))),
            count(*) FILTER (WHERE length <= 0 AND NOT (lat1 = lat2 AND lon1 = lon2))
        FROM segments WHERE city = ?""", city_id)
    # Ways that touch the city are kept whole (a road leaving the bbox keeps its outside part),
    # but every kept way must have at least one point inside.
    stray = q("""SELECT count(*) FROM (SELECT way FROM segments WHERE city = ? GROUP BY way
                 HAVING NOT bool_or((lat1 BETWEEN ? AND ? AND lon1 BETWEEN ? AND ?)
                                 OR (lat2 BETWEEN ? AND ? AND lon2 BETWEEN ? AND ?)))""",
              city_id, c.south, c.north, c.west, c.east, c.south, c.north, c.west, c.east)[0]
    report(f"{city_id} segments", total=n, non_finite=bad, nonpositive_length=nonpos, ways_entirely_outside=stray)
    assert n > 100_000 and bad == 0 and nonpos == 0 and stray == 0


@needs_data
@pytest.mark.parametrize("city_id", CITY_IDS)
def test_segment_lengths_match_their_coordinates(city_id):
    from safeher.geo import haversine_m
    rows = store.con().execute("SELECT lat1, lon1, lat2, lon2, length FROM segments WHERE city = ? "
                               "USING SAMPLE 20000 ROWS", [city_id]).fetchall()
    off = sum(1 for a, b, c_, d, length in rows if abs(haversine_m((a, b), (c_, d)) - length) > 0.01)
    report(f"{city_id} segment lengths", sampled=len(rows), off_by_over_1cm=off)
    assert off == 0


@needs_data
@pytest.mark.parametrize("city_id", CITY_IDS)
def test_street_tags_use_known_values(city_id):
    hw = dict(store.con().execute("SELECT highway, count(*) FROM segments WHERE city = ? GROUP BY 1", [city_id]).fetchall())
    lit = dict(store.con().execute("SELECT lit, count(*) FROM segments WHERE city = ? AND lit IS NOT NULL GROUP BY 1",
                                   [city_id]).fetchall())
    unknown_hw = {k: c for k, c in hw.items() if k not in KNOWN_HIGHWAYS}
    unknown_lit = {k: c for k, c in lit.items() if k not in KNOWN_LIT}
    lit_share = sum(lit.values()) / sum(hw.values())
    report(f"{city_id} tags", highway_kinds=len(hw), unknown_highway=unknown_hw, lit_tagged=f"{lit_share:.2%}",
           unknown_lit=unknown_lit)
    assert sum(unknown_hw.values()) / sum(hw.values()) < 0.001
    assert sum(unknown_lit.values()) / max(1, sum(lit.values())) < 0.001  # mappers' typos, ignored by scoring


@needs_data
@pytest.mark.parametrize("city_id", CITY_IDS)
def test_places_are_valid_unique_and_inside_city(city_id):
    c = CITIES[city_id]
    n, dupes, outside, blank = q("""SELECT count(*), count(*) - count(DISTINCT osm),
            count(*) FILTER (WHERE NOT (lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?)),
            count(*) FILTER (WHERE json_extract_string(tags, '$.name') IS NOT NULL
                                   AND trim(json_extract_string(tags, '$.name')) = '')
        FROM places WHERE city = ?""", c.south, c.north, c.west, c.east, city_id)
    report(f"{city_id} places", total=n, duplicate_ids=dupes, outside_bbox=outside, blank_names=blank)
    assert n > 10_000 and dupes == 0 and outside == 0 and blank == 0


@needs_data
@pytest.mark.parametrize("city_id", CITY_IDS)
def test_every_quick_action_has_places_city_wide(city_id):
    kinds = Counter(classify(p["tags"]) for p in store.places(city_id))
    report(f"{city_id} quick-action coverage", **{k: kinds[k] for k in ("police", "toilets", "hospital", "pharmacy",
                                                                        "bus_stop", "metro", "street_lamp")})
    for k in ("police", "toilets", "hospital", "pharmacy"):
        assert kinds[k] >= 20, f"only {kinds[k]} {k} mapped in {city_id}"
    assert kinds["bus_stop"] + kinds["metro"] + kinds["rail"] > 500


@needs_data
@pytest.mark.parametrize("city_id", CITY_IDS)
def test_night_lights_cover_the_city(city_id):
    c = CITIES[city_id]
    n, lo, hi, outside = q("""SELECT count(*), min(radiance), max(radiance),
            count(*) FILTER (WHERE NOT (lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?))
        FROM night_lights WHERE city = ?""", c.south - 0.01, c.north + 0.01, c.west - 0.01, c.east + 0.01, city_id)
    report(f"{city_id} night lights", pixels=n, radiance=f"{lo:.1f}..{hi:.1f}", outside=outside)
    # ~500 m pixels: the bbox should be close to fully covered
    expected = (c.north - c.south) / 0.004167 * (c.east - c.west) / 0.004167
    assert n > 0.8 * expected and lo >= 0 and outside == 0


@needs_data
@pytest.mark.parametrize("city_id", CITY_IDS)
def test_districts_and_ncrb_line_up(city_id):
    c = CITIES[city_id]
    districts = {r[0] for r in store.con().execute("SELECT name FROM districts WHERE city = ?", [city_id]).fetchall()}
    incidents = store.con().execute("SELECT district, avg_cases, percentile, confidence FROM incidents "
                                    "WHERE city = ?", [city_id]).fetchall()
    report(f"{city_id} districts", osm=len(districts), ncrb=len(incidents))
    if not c.districts:
        assert not incidents  # no sub-city data: the signal is left out, never guessed
        return
    mapping = c.districts["osm_to_ncrb"]
    assert set(mapping) <= districts, f"OSM districts missing: {set(mapping) - districts}"
    ncrb = {d for d, *_ in incidents}
    assert set(mapping.values()) <= ncrb
    assert all(a >= 0 and 0 <= p <= 1 and 0 < conf <= 0.2 for _, a, p, conf in incidents)


@needs_data
def test_district_lookup_matches_known_places():
    delhi = CITIES["delhi"]
    for (lat, lon), osm in {(28.5433, 77.2067): "South Delhi", (28.6315, 77.2167): "New Delhi",
                            (28.5523, 77.0583): "South West Delhi"}.items():
        assert store.district_at(delhi, (lat, lon)) == osm
    pct, conf, note = store.incident_at(delhi, (28.6315, 77.2167))
    assert note.startswith("New Delhi police district") and 0 <= pct <= 1


# --- Trip corridors (the walking graph actually routed on) --------------------------------------


@needs_data
@pytest.mark.parametrize("city_id", CITY_IDS)
def test_corridor_nodes_are_inside_the_corridor(city_id):
    _, _, _, data = default_corridor(city_id)
    a = data.area
    outside = [n for n, d in data.graph.nodes(data=True)
               if not (a.south <= d["y"] <= a.north and a.west <= d["x"] <= a.east)]
    report(f"{city_id} corridor", nodes=data.graph.number_of_nodes(), edges=data.graph.number_of_edges(),
           places=len(data.pois), outside=len(outside))
    assert not outside and data.graph.number_of_nodes() > 500


@needs_data
@pytest.mark.parametrize("city_id", CITY_IDS)
def test_corridor_edge_lengths_match_their_geometry(city_id):
    _, _, _, data = default_corridor(city_id)
    ratios, nonpositive = [], 0
    for u, v, k, d in data.graph.edges(keys=True, data=True):
        if d["length"] <= 0:
            nonpositive += 1
            continue
        pts = [(y, x) for x, y in d["geometry"].coords] if "geometry" in d else [data.node_latlon(u), data.node_latlon(v)]
        geom = polyline_length_m(pts) if u != v else d["length"]
        ratios.append(geom / d["length"])
    ratios.sort()
    off = sum(1 for r in ratios if abs(r - 1) > 0.02)
    report(f"{city_id} corridor edge lengths", edges=len(ratios), nonpositive=nonpositive,
           off_by_over_2pct=off, worst=f"{ratios[0]:.3f}..{ratios[-1]:.3f}")
    assert nonpositive == 0 and off / len(ratios) < 0.001


@needs_data
@pytest.mark.parametrize("city_id", CITY_IDS)
def test_corridor_graph_is_walkable_both_ways(city_id):
    _, _, _, data = default_corridor(city_id)
    g = data.graph
    one_way = sum(1 for u, v in g.edges() if not g.has_edge(v, u))
    report(f"{city_id} corridor connectivity", one_way_edges=one_way)
    assert one_way == 0  # walking ignores one-way rules


@needs_data
@pytest.mark.parametrize("city_id", CITY_IDS)
def test_opening_hours_parse_or_fall_back_safely(city_id):
    from datetime import datetime
    _, _, _, data = default_corridor(city_id)
    tagged = [p for p in data.pois if p.opening_hours]
    parsed = [p for p in tagged if p._parsed_hours is not None]
    when = datetime(2026, 10, 14, 21, 30)
    for p in data.pois:  # never raises, always returns (bool, bool)
        is_open, assumed = p.is_open(when)
        assert isinstance(is_open, bool) and isinstance(assumed, bool)
        assert assumed == (p._parsed_hours is None)
    report(f"{city_id} opening hours", with_tag=len(tagged), parsed=len(parsed), unparseable=len(tagged) - len(parsed))
    assert len(parsed) >= 0.8 * len(tagged)


@needs_data
@pytest.mark.parametrize("city_id", CITY_IDS)
def test_police_and_hospitals_are_reachable_from_the_network(city_id):
    _, _, _, data = default_corridor(city_id)
    help_points = [p for p in data.pois if p.kind in ("police", "hospital")]
    far = [p.name for p in help_points if data.nodes.nearest(p.latlon, MAX_SNAP_M) is None]
    report(f"{city_id} reachable help points", total=len(help_points), far_from_streets=far)
    assert help_points and not far


@needs_data
@pytest.mark.parametrize("city_id", CITY_IDS)
def test_landmarks_snap_to_streets_and_default_trip_plans(city_id):
    from datetime import datetime
    from safeher.plan import plan
    c, o, d, data = default_corridor(city_id)
    snaps = {lm.name: round(data.nodes.nearest((lm.lat, lm.lon), MAX_SNAP_M)[0])
             for lm in c.landmarks if data.area.contains(lm.lat, lm.lon)}
    report(f"{city_id} landmark snap distances (m)", **snaps)
    assert max(snaps.values()) < 150
    p = plan((o.lat, o.lon), (d.lat, d.lon), datetime(2026, 10, 14, 21, 30, tzinfo=c.tz))
    assert len(p["routes"]) >= 2


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
    assert a["default_city"] in CITIES
    t = a["trips"]
    assert 0 < t["max_trip_km"] <= 20 and t["corridor_margin_m"] > 0 and t["corridor_cache_size"] >= 2
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


def test_cities_config_is_internally_consistent():
    c = config.load("cities")
    assert c["night_lights"]["year"] >= 2012 and "2020" not in c["ncrb"]["years"]  # lockdown year skipped
    assert not any("husband" in h or "dowry" in h or "domestic" in h for h in c["ncrb"]["heads"])  # public space only
    boxes = [(x.south, x.west, x.north, x.east) for x in CITIES.values()]
    for i, (s1, w1, n1, e1) in enumerate(boxes):  # a point belongs to at most one city
        for s2, w2, n2, e2 in boxes[i + 1:]:
            assert n1 < s2 or n2 < s1 or e1 < w2 or e2 < w1
    for city in c["cities"]:
        assert city["extract"].endswith(".osm.pbf")
        if city["districts"]:
            assert len(set(city["districts"]["osm_to_ncrb"].values())) == len(city["districts"]["osm_to_ncrb"])
