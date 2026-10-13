"""Fuzz every endpoint with random and malformed input. Nothing may ever return a 5xx."""

from __future__ import annotations

import math
import string

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from safeher import api, trips
from safeher.areas import AREAS

pytestmark = pytest.mark.skipif(
    not all((a.dir / "walk.graphml").exists() for a in AREAS.values()), reason="OSM snapshots not fetched")

EXAMPLES = int(__import__("os").environ.get("SAFEHER_FUZZ_EXAMPLES", "120"))
FUZZ = settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=list(HealthCheck))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    d = tmp_path_factory.mktemp("fuzz")
    api.REPORTS_FILE = d / "reports.jsonl"
    trips.DB_FILE = d / "trips.sqlite3"
    import os
    os.environ["SAFEHER_LLM"] = "template"
    return TestClient(api.app, raise_server_exceptions=False)


def no_5xx(r):
    assert r.status_code < 500, f"{r.request.method} {r.request.url} → {r.status_code}: {r.text[:300]}"


# Coordinates: inside each area, near the edges, outside, and nonsense.
def in_area():
    return st.sampled_from(sorted(AREAS)).flatmap(lambda a: st.tuples(
        st.floats(AREAS[a].south, AREAS[a].north), st.floats(AREAS[a].west, AREAS[a].east)))


weird_numbers = st.one_of(st.floats(allow_nan=True, allow_infinity=True), st.integers(-10**12, 10**12),
                          st.just(1e308), st.just(-0.0))
coord = st.one_of(in_area(), st.tuples(weird_numbers, weird_numbers))
text = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=200)
times = st.one_of(st.none(), st.just("2026-10-14T21:30:00"), st.just("2026-10-14T23:59:59+05:30"),
                  st.just("1900-01-01T00:00:00"), st.just("9999-12-31T23:59:59"), st.just("2026-02-30T10:00:00"),
                  text)


def point(p):
    lat, lon = p
    return {"lat": lat if math.isfinite(lat) else str(lat), "lon": lon if math.isfinite(lon) else str(lon)}


@FUZZ
@given(o=coord, d=coord, t=times)
def test_plan_never_crashes(client, o, d, t):
    r = client.post("/api/plan", json={"origin": point(o), "destination": point(d), "depart_at": t})
    no_5xx(r)
    if r.status_code == 200:
        for route in r.json()["routes"]:
            assert route["score"] is None or 0 <= route["score"] <= 100


@FUZZ
@given(o=in_area(), d=in_area(), t=times, lang=st.one_of(st.sampled_from(["en", "hi"]), text))
def test_explain_never_crashes(client, o, d, t, lang):
    no_5xx(client.post("/api/explain", json={"origin": point(o), "destination": point(d), "depart_at": t, "lang": lang}))


@FUZZ
@given(body=st.one_of(st.none(), st.integers(), text, st.lists(st.integers()), st.dictionaries(text, text)))
def test_malformed_json_bodies(client, body):
    for path in ("/api/plan", "/api/explain", "/api/reports", "/api/trips"):
        no_5xx(client.post(path, json=body))


@FUZZ
@given(q=text, area=st.one_of(st.none(), st.sampled_from(sorted(AREAS)), text), limit=st.integers(-5, 10_000))
def test_search_never_crashes(client, q, area, limit):
    params = {"q": q, "limit": limit}
    if area is not None:
        params["area"] = area
    no_5xx(client.get("/api/search", params=params))


@FUZZ
@given(p=coord, kind=st.one_of(st.sampled_from(["police", "toilets", "hospital", "pharmacy", "open_places"]), text),
       t=times, limit=st.integers(-5, 10_000))
def test_nearby_never_crashes(client, p, kind, t, limit):
    params = {"lat": p[0], "lon": p[1], "kind": kind, "limit": limit}
    if t is not None:
        params["depart_at"] = t
    no_5xx(client.get("/api/nearby", params=params))


@FUZZ
@given(p=coord, cat=st.one_of(st.sampled_from(["harassment", "positive", "broken_light"]), text),
       note=st.one_of(st.none(), text, st.text(min_size=281, max_size=400)))
def test_reports_never_crash(client, p, cat, note):
    no_5xx(client.post("/api/reports", json={**point(p), "category": cat, "note": note}))


@FUZZ
@given(route=st.lists(st.tuples(weird_numbers, weird_numbers), max_size=50),
       token=st.text(alphabet=string.ascii_letters + string.digits + "-_%/.", max_size=80),
       loc=st.tuples(weird_numbers, weird_numbers, weird_numbers))
def test_live_sharing_never_crashes(client, route, token, loc):
    as_json = lambda v: v if math.isfinite(v) else str(v)  # noqa: E731
    r = client.post("/api/trips", json={"route": [[as_json(a), as_json(b)] for a, b in route]})
    no_5xx(r)
    lat, lon, acc = loc
    body = {"lat": lat if math.isfinite(lat) else 0, "lon": lon if math.isfinite(lon) else 0,
            "accuracy_m": acc if math.isfinite(acc) else 1}
    no_5xx(client.get(f"/api/trips/{token}"))
    no_5xx(client.put(f"/api/trips/{token}/location", json=body, headers={"Authorization": f"Bearer {token}"}))
    no_5xx(client.delete(f"/api/trips/{token}", headers={"Authorization": "Bearer x"}))
    if r.status_code == 201:  # the real owner can update and stop; a wrong token can't
        t = r.json()
        good = client.put(f"/api/trips/{t['viewer_token']}/location", json={"lat": 28.54, "lon": 77.21, "accuracy_m": 5},
                          headers={"Authorization": f"Bearer {t['owner_token']}"})
        assert good.status_code == 204
        bad = client.put(f"/api/trips/{t['viewer_token']}/location", json={"lat": 1, "lon": 1, "accuracy_m": 5},
                         headers={"Authorization": f"Bearer {t['viewer_token']}"})
        assert bad.status_code == 403
        assert client.delete(f"/api/trips/{t['viewer_token']}",
                             headers={"Authorization": f"Bearer {t['owner_token']}"}).status_code == 204


@FUZZ
@given(path=st.text(alphabet=string.ascii_letters + string.digits + "/-_.%~", max_size=60))
def test_unknown_and_google_paths_never_crash(client, path):
    for prefix in ("/api/", "/api/layers/", "/api/google/tiles/light/", "/api/google/place/", "/static/"):
        no_5xx(client.get(prefix + path))
