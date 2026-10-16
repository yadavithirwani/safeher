import pytest
from fastapi.testclient import TestClient

from safeher import api, store
from safeher.cities import CITIES
from safeher.search import _match_rank

pytestmark = pytest.mark.skipif(not store.available(), reason="run: python -m safeher.data build")

BLR, DEL = "bengaluru", "delhi"
INDIRANAGAR = {"lat": 12.9719, "lon": 77.6412}


@pytest.fixture(scope="module")
def client():
    return TestClient(api.app)


def find(client, q, city=None, **near):
    return client.get("/api/search", params={"q": q, **({"city": city} if city else {}), **near}).json()


def test_street_names_are_searchable(client):
    r = find(client, "aurobindo marg", DEL)
    assert r and r[0]["name"] == "Aurobindo Marg" and r[0]["kind"] == "street"


def test_word_prefix_matching(client):
    assert any(x["name"] == "12th Main Road" for x in find(client, "12th main", BLR))


def test_same_name_places_are_told_apart(client):
    hits = [x for x in find(client, "starbucks", BLR) if x["name"] == "Starbucks"]
    assert len(hits) >= 2
    assert len({x["detail"] for x in hits}) == len(hits)  # each one has a distinguishing street


def test_any_named_place_in_the_city_is_found(client):
    """Not only places used for scoring: bars, attractions and neighbourhoods too."""
    assert any(x["name"] == "Toit" and x["kind"] == "pub" for x in find(client, "toit", BLR))
    assert any(x["name"] == "Bob's Bar" for x in find(client, "bobs bar", BLR))
    assert any(x["name"] == "Koramangala" and x["kind"] == "area" for x in find(client, "koramangala", BLR))
    assert any(x["name"] == "India Gate" for x in find(client, "india gate", DEL))


def test_results_show_their_neighbourhood(client):
    toit = next(x for x in find(client, "toit", BLR) if x["name"] == "Toit")
    assert toit["neighbourhood"] and toit["neighbourhood"] in toit["detail"]


def test_same_name_matches_nearest_to_the_map_first(client):
    hits = [x for x in find(client, "starbucks", BLR, **INDIRANAGAR) if x["name"] == "Starbucks"]
    from safeher.geo import haversine_m
    d = [haversine_m((INDIRANAGAR["lat"], INDIRANAGAR["lon"]), (x["lat"], x["lon"])) for x in hits]
    assert d[0] < 3000 and d == sorted(d, key=lambda m: round(m / 1000))


def test_city_defaults_to_the_one_on_screen(client):
    assert find(client, "toit", **INDIRANAGAR)[0]["city_id"] == BLR


def test_other_city_results_are_flagged(client):
    r = find(client, "hauz khas", BLR)
    assert r and all(x.get("other_city") and x["city_id"] == DEL for x in r)


def test_current_city_results_are_not_flagged(client):
    assert not any(x.get("other_city") for x in find(client, "hauz khas", DEL))


def test_no_match_returns_empty(client):
    assert find(client, "zzqqxx", DEL) == []


def test_unknown_city_is_404(client):
    assert client.get("/api/search", params={"q": "x", "city": "mumbai"}).status_code == 404


def test_results_are_inside_their_city(client):
    for q, city in [("metro", DEL), ("road", BLR), ("hospital", BLR)]:
        for x in find(client, q, city):
            assert CITIES[x["city_id"]].contains(x["lat"], x["lon"])


@pytest.mark.parametrize("query, name, rank", [
    ("Saket", "saket", 0), ("hauz", "Hauz Khas Metro", 1), ("khas metro", "Hauz Khas Metro", 2),
    ("citywalk", "Select Citywalk, Saket", 2), ("walk", "Select Citywalk, Saket", 3), ("xyz", "Saket", None),
])
def test_match_rank(query, name, rank):
    assert _match_rank(query, name) == rank


def test_apostrophes_are_ignored():
    """Found with real data: "bobs bar" didn't match "Bob's Bar"."""
    assert _match_rank("bobs bar", "Bob's Bar") == 0
    assert _match_rank("bob's", "Bobs Cafe") == 1
