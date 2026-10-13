import pytest
from fastapi.testclient import TestClient

from safeher import api
from safeher.areas import AREAS
from safeher.search import _match_rank

pytestmark = pytest.mark.skipif(
    not all((a.dir / "pois.json").exists() for a in AREAS.values()), reason="OSM snapshots not fetched")

BLR, DEL = "blr-indiranagar-mg-road", "del-hauz-khas-saket"


@pytest.fixture(scope="module")
def client():
    return TestClient(api.app)


def find(client, q, area=None):
    return client.get("/api/search", params={"q": q, **({"area": area} if area else {})}).json()


def test_street_names_are_searchable(client):
    r = find(client, "aurobindo marg", DEL)
    assert r and r[0]["name"] == "Aurobindo Marg" and r[0]["kind"] == "street"


def test_word_prefix_matching(client):
    assert any(x["name"] == "12th Main Road" for x in find(client, "12th main", BLR))


def test_same_name_places_are_told_apart(client):
    hits = [x for x in find(client, "starbucks", BLR) if x["name"] == "Starbucks"]
    assert len(hits) >= 2
    assert len({x["detail"] for x in hits}) == len(hits)  # each one has a distinguishing street


def test_other_city_results_are_flagged(client):
    r = find(client, "hauz khas", BLR)
    assert r and all(x.get("other_area") and x["area"] == DEL for x in r)


def test_current_city_results_are_not_flagged(client):
    assert not any(x.get("other_area") for x in find(client, "hauz khas", DEL))


def test_no_match_returns_empty(client):
    assert find(client, "zzqqxx", DEL) == []


def test_unknown_area_is_404(client):
    assert client.get("/api/search", params={"q": "x", "area": "mumbai"}).status_code == 404


def test_results_are_inside_their_area(client):
    for q, area in [("metro", DEL), ("road", BLR), ("hospital", BLR)]:
        for x in find(client, q, area):
            assert AREAS[x["area"]].contains(x["lat"], x["lon"])


@pytest.mark.parametrize("query, name, rank", [
    ("Saket", "saket", 0), ("hauz", "Hauz Khas Metro", 1), ("khas metro", "Hauz Khas Metro", 2),
    ("citywalk", "Select Citywalk, Saket", 2), ("walk", "Select Citywalk, Saket", 3), ("xyz", "Saket", None),
])
def test_match_rank(query, name, rank):
    assert _match_rank(query, name) == rank
