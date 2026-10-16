"""End-to-end checks on random trips, plus invalid requests.

Trips are drawn from anywhere on each supported city's street network with a seeded RNG:
    SAFEHER_SEED=42 SAFEHER_TRIPS=2 .venv/bin/pytest tests/test_random_trips.py -s
The seed is printed so any failure can be reproduced exactly.
"""

from __future__ import annotations

import math
import os
import random
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from safeher import api, store
from safeher import signals as sig
from safeher.plan import TripContext, edge_support, score_pieces
from safeher.pois import PointIndex
from safeher.routing import candidate_paths, path_edges
from safeher.scoring import night_fraction, score_route, weights_at
from safeher.cities import CITIES, corridor
from safeher.explain import check, facts_from_plan
from safeher.geo import haversine_m, polyline_distance_m, polyline_length_m
from safeher.scoring import BANDS

SEED = int(os.environ.get("SAFEHER_SEED", "20261013"))
TRIPS_PER_CITY = int(os.environ.get("SAFEHER_TRIPS", "2"))
TIMES = ["13:00", "21:30", "23:45"]

pytestmark = pytest.mark.skipif(not store.available(), reason="run: python -m safeher.data build")


def random_trips() -> list[tuple[str, tuple, tuple, str]]:
    """(city_id, origin, destination, HH:MM): street corners anywhere in the city, 0.8-3 km apart."""
    if not store.available():
        return []
    rng = random.Random(SEED)
    trips = []
    for city_id in sorted(CITIES):
        c = CITIES[city_id]
        n = store.con().execute("SELECT count(*) FROM segments WHERE city = ?", [city_id]).fetchone()[0]
        while sum(t[0] == city_id for t in trips) < TRIPS_PER_CITY:
            lat, lon = store.con().execute("SELECT lat1, lon1 FROM segments WHERE city = ? ORDER BY way, u, v "
                                           "LIMIT 1 OFFSET ?", [city_id, rng.randrange(n)]).fetchone()
            bearing, dist = rng.uniform(0, 2 * math.pi), rng.uniform(1000, 2500)
            far = (lat + dist * math.cos(bearing) / 111_320,
                   lon + dist * math.sin(bearing) / (111_320 * math.cos(math.radians(lat))))
            if not (c.contains(lat, lon) and c.contains(*far)):
                continue
            data = store.corridor_data(corridor((lat, lon), far))
            snapped = [data.nodes.nearest(p, 150) for p in ((lat, lon), far)]
            if None in snapped:
                continue  # e.g. a lake or an airfield: try elsewhere
            o, d = (data.node_latlon(n_) for _, n_ in snapped)
            if 800 <= haversine_m(o, d) <= 3000:
                trips.append((city_id, o, d, rng.choice(TIMES)))
    return trips


TRIPS = random_trips()
print(f"\n[random trips] SAFEHER_SEED={SEED}: " + "; ".join(
    f"{a} {o[0]:.5f},{o[1]:.5f} → {d[0]:.5f},{d[1]:.5f} at {t}" for a, o, d, t in TRIPS))


@pytest.fixture(scope="module")
def client():
    os.environ["SAFEHER_LLM"] = "template"
    yield TestClient(api.app)
    os.environ.pop("SAFEHER_LLM", None)


def body(o, d, t, **extra):
    return {"origin": {"lat": o[0], "lon": o[1]}, "destination": {"lat": d[0], "lon": d[1]},
            "depart_at": f"2026-10-13T{t}:00", **extra}


@pytest.mark.parametrize("trip", TRIPS, ids=lambda t: f"{t[0]}@{t[3]}")
def test_random_trip_all_features(client, trip):
    city_id, o, d, t = trip
    res = client.post("/api/plan", json=body(o, d, t))
    assert res.status_code == 200, res.text
    p = res.json()
    routes = p["routes"]
    straight = haversine_m(o, d)
    print(f"\n  {city_id} {t}: straight {straight:.0f} m → " + ", ".join(
        f"{r['label']} {r['distance_m']} m/{r['duration_min']} min score {r['score']}" for r in routes))

    # Routes: 1-3, distinct labels, sorted by score, within the detour limit.
    assert 1 <= len(routes) <= 3 and len({r["label"] for r in routes}) == len(routes)
    assert "Shortest" in {r["label"] for r in routes}
    scores = [r["score"] for r in routes if r["score"] is not None]
    assert scores == sorted(scores, reverse=True)
    shortest = next(r for r in routes if r["label"] == "Shortest")
    for r in routes:
        assert straight * 0.99 <= r["distance_m"] <= 1.5 * shortest["distance_m"] + 1
        assert r["duration_min"] == round(r["distance_m"] / 80)
        # Starts and ends at the requested points (they are street nodes).
        assert haversine_m(tuple(r["geometry"][0]), o) < 5 and haversine_m(tuple(r["geometry"][-1]), d) < 5
        # Pieces tile the route: their lengths add up to the route distance.
        piece_len = sum(polyline_length_m([tuple(c) for c in pc["coords"]]) for pc in r["pieces"])
        assert abs(piece_len - r["distance_m"]) <= 2
        # Score, band and breakdown are consistent.
        if r["score"] is None:
            assert r["band"] == "Limited data"
        else:
            assert 0 <= r["score"] <= 100
            assert r["band"] == next(label for floor, label in BANDS if r["score"] >= floor)
        assert [b["signal"] for b in r["breakdown"]] == ["lighting", "transit", "safe_spaces", "incidents", "reports"]
        assert abs(sum(b["base_weight"] for b in r["breakdown"]) - 1) < 0.01
        for b in r["breakdown"]:
            assert b["value"] is None or 0 <= b["value"] <= 100
            assert 0 <= b["confidence"] <= 1
        line = [tuple(c) for c in r["geometry"]]
        for w in r["weak_stretches"]:
            assert w["length_m"] >= 100 and w["score"] < 50 and w["main_factors"]
        for s in r["stops"]:
            assert s["kind"] in {"police", "toilets", "hospital", "pharmacy"}
            assert polyline_distance_m((s["lat"], s["lon"]), line) <= 300 + 30
            assert 0 <= s["along_route_m"] <= r["distance_m"] + 30
        assert "safe " not in " ".join(r["reasons"]).lower() + " " and "unsafe" not in " ".join(r["reasons"]).lower()

    # Explanations in both languages pass the guardrails and state the scores.
    facts = facts_from_plan(p)
    for lang in ("en", "hi"):
        e = client.post("/api/explain", json=body(o, d, t, lang=lang)).json()
        assert e["checked"] and e["lang"] == lang
        assert check(e["text"], facts, lang).ok, e["text"]
        print(f"    explain[{lang}]: {e['text'][:140]}{'…' if len(e['text']) > 140 else ''}")

    # Nearby, for every category, from the origin.
    for kind in ("police", "toilets", "hospital", "pharmacy", "open_places"):
        n = client.get("/api/nearby", params={"lat": o[0], "lon": o[1], "kind": kind,
                                               "depart_at": f"2026-10-13T{t}:00"}).json()["results"]
        dists = [x["distance_m"] for x in n]
        assert dists == sorted(dists) and all(x <= 2500 for x in dists)
        if kind == "open_places":
            assert all(x["open"] for x in n)
        else:
            assert all(x["kind"] == kind for x in n)

    # Reporting works through the API (stored at a street node, plans still succeed)…
    top = routes[0]
    mid = top["geometry"][len(top["geometry"]) // 2]
    rep = client.post("/api/reports", json={"lat": mid[0], "lon": mid[1], "category": "harassment"})
    assert rep.status_code == 201 and rep.json()["verification"] == "pending"
    assert client.post("/api/plan", json=body(o, d, t)).status_code == 200
    # …and on the exact same path, a harassment report lowers the score and never raises it.
    assert_report_lowers_same_path(o, d, t)


def assert_report_lowers_same_path(o, d, t):
    data = store.corridor_data(corridor(o, d))
    when = datetime.fromisoformat(f"2026-10-13T{t}:00").replace(tzinfo=data.area.tz)
    night = night_fraction(when, *data.area.center)
    w = weights_at(night)
    clean = TripContext(data, when, night, w)
    nodes = candidate_paths(data, o, d, edge_support(clean))[0][1]
    edges = path_edges(data, nodes)
    spot = edges[len(edges) // 2].points[0]
    reported = TripContext(data, when, night, w, PointIndex([(spot, sig.Report("harassment", when))]))
    before = score_pieces(clean, edges)
    after = score_pieces(reported, edges)
    pairs = [(b.segment.score, a.segment.score) for b, a in zip(before, after) if b.segment.score is not None]
    assert all(a <= b + 1e-9 for b, a in pairs), "a harassment report raised a stretch's score"
    assert any(a < b for b, a in pairs), "the report had no effect on the stretches around it"
    rs = lambda pieces: score_route([(pc.length_m, pc.segment) for pc in pieces], w).score
    assert rs(after) <= rs(before)


# --- Invalid input ------------------------------------------------------------------------

DEL = (28.5433, 77.2067)
BLR = (12.9784, 77.6408)


@pytest.mark.parametrize("name, method, path, payload, status, detail", [
    ("origin outside supported cities", "post", "/api/plan", body((19.07, 72.87), DEL, "21:30"), 422, "only"),
    ("too far to walk", "post", "/api/plan", body(DEL, (28.70, 77.30), "21:30"), 422, "plans walks up to"),
    ("trip across two cities", "post", "/api/plan", body(DEL, BLR, "21:30"), 422, "same city"),
    ("same start and end", "post", "/api/plan", body(DEL, DEL, "21:30"), 422, "same place"),
    ("latitude out of range", "post", "/api/plan", body((999, 77.2), DEL, "21:30"), 422, None),
    ("missing destination", "post", "/api/plan", {"origin": {"lat": DEL[0], "lon": DEL[1]}}, 422, None),
    ("unreadable departure time", "post", "/api/plan",
     {**body(DEL, (28.5286, 77.2195), "21:30"), "depart_at": "tomorrow evening"}, 422, None),
    ("unsupported language", "post", "/api/explain", body(DEL, (28.5286, 77.2195), "21:30", lang="fr"), 422, None),
    ("unknown report category", "post", "/api/reports", {"lat": DEL[0], "lon": DEL[1], "category": "spam"}, 422, None),
    ("report outside supported cities", "post", "/api/reports", {"lat": 19.07, "lon": 72.87, "category": "harassment"},
     422, "only accepted in"),
    ("report note too long", "post", "/api/reports",
     {"lat": DEL[0], "lon": DEL[1], "category": "harassment", "note": "x" * 300}, 422, None),
    ("unknown nearby category", "get", "/api/nearby", {"lat": DEL[0], "lon": DEL[1], "kind": "bars"}, 422, None),
    ("nearby outside supported cities", "get", "/api/nearby", {"lat": 19.07, "lon": 72.87, "kind": "police"}, 422, "only"),
    ("empty search", "get", "/api/search", {"q": ""}, 422, None),
    ("map layers without a box", "get", "/api/layers", None, 422, None),
    ("map layers for a whole city", "get", "/api/layers",
     {"south": 28.40, "west": 76.83, "north": 28.89, "east": 77.35}, 422, "Zoom in"),
    ("unknown search city", "get", "/api/search", {"q": "metro", "city": "mumbai"}, 404, None),
])
def test_invalid_requests_rejected(client, name, method, path, payload, status, detail):
    if method == "post":
        r = client.post(path, json=payload)
    else:
        r = client.get(path, params=payload)
    assert r.status_code == status, f"{name}: {r.status_code} {r.text[:200]}"
    if detail:
        assert detail in r.json()["detail"], name


def test_invalid_report_left_no_data(client):
    """Rejected reports must not be stored."""
    rows = client.get("/api/reports").json()
    assert all(r["category"] in api.sig.REPORT_SEVERITY for r in rows)
    assert not any(abs(r["lat"] - 19.07) < 0.1 for r in rows)


def test_timezone_aware_departure_respected(client):
    """16:00 UTC is 21:30 in India; the plan must use local time."""
    r = client.post("/api/plan", json={**body(DEL, (28.5286, 77.2195), "21:30"), "depart_at": "2026-10-13T16:00:00Z"})
    assert r.json()["departure_local"].startswith("2026-10-13T21:30")


def test_point_off_the_street_network_is_rejected(client):
    """A point inside a city but far from any walkable street is refused, not silently moved.
    (Turahalli forest, Bengaluru: no street within 300 m.)"""
    forest = (12.8625, 77.4945)
    r = client.post("/api/plan", json=body(forest, (12.8800, 77.5100), "21:30"))
    assert r.status_code == 422 and "no walkable street" in r.json()["detail"]
