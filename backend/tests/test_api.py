import pytest
from fastapi.testclient import TestClient

from safeher import api
from safeher.areas import AREAS

pytestmark = pytest.mark.skipif(
    not all((a.dir / "walk.graphml").exists() for a in AREAS.values()),
    reason="OSM snapshots not fetched (python -m safeher.fetch)",
)

HAUZ_KHAS, SAKET = {"lat": 28.5433, "lon": 77.2067}, {"lat": 28.5286, "lon": 77.2195}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "REPORTS_FILE", tmp_path / "reports.jsonl")
    return TestClient(api.app)


def test_areas_have_landmarks(client):
    areas = client.get("/api/areas").json()
    assert {a["id"] for a in areas} == set(AREAS)
    assert all(len(a["landmarks"]) >= 2 for a in areas)


def test_plan_returns_routes_with_pieces(client):
    r = client.post("/api/plan", json={"origin": HAUZ_KHAS, "destination": SAKET, "depart_at": "2026-10-13T21:30:00"})
    assert r.status_code == 200
    body = r.json()
    assert body["departure_local"].endswith("+05:30")  # naive time read as local area time
    route = body["routes"][0]
    assert route["pieces"] and all("score" in p and "light" in p for p in route["pieces"])
    for w in route["weak_stretches"]:  # endpoints match piece coordinates so the UI can highlight them
        assert any(p["coords"][0] == w["start"] for p in route["pieces"])


def test_plan_outside_demo_areas_is_422(client):
    r = client.post("/api/plan", json={"origin": {"lat": 19.07, "lon": 72.87}, "destination": {"lat": 19.08, "lon": 72.88}})
    assert r.status_code == 422 and "demo areas" in r.json()["detail"]


def test_search_finds_landmarks_first(client):
    names = [r["name"] for r in client.get("/api/search", params={"q": "hauz"}).json()]
    assert names[0] == "Hauz Khas Metro"


def test_nearby_police_sorted_and_deduplicated(client):
    res = client.get("/api/nearby", params={"lat": 12.9784, "lon": 77.6408, "kind": "police"}).json()["results"]
    dists = [r["distance_m"] for r in res]
    assert dists == sorted(dists)
    names = [r["name"] for r in res]
    assert len(names) == len(set(names))


def test_report_is_snapped_and_changes_scores(client):
    body = {"origin": HAUZ_KHAS, "destination": SAKET, "depart_at": "2026-10-13T21:30:00"}
    before = client.post("/api/plan", json=body).json()["routes"]
    target = before[0]["geometry"][len(before[0]["geometry"]) // 2]
    created = client.post("/api/reports", json={"lat": target[0] + 0.00005, "lon": target[1], "category": "harassment"})
    assert created.status_code == 201
    row = created.json()
    assert row["verification"] == "pending"
    assert (row["lat"], row["lon"]) != (target[0] + 0.00005, target[1])  # stored at a street node, not the tap
    after = {r["label"]: r for r in client.post("/api/plan", json=body).json()["routes"]}
    assert after[before[0]["label"]]["score"] <= before[0]["score"]
    assert len(client.get("/api/reports").json()) == 1


def test_report_validation(client):
    assert client.post("/api/reports", json={"lat": 28.54, "lon": 77.21, "category": "spam"}).status_code == 422
    assert client.post("/api/reports", json={"lat": 19.0, "lon": 72.8, "category": "harassment"}).status_code == 422


def test_layers_and_web_app(client):
    fc = client.get("/api/layers/blr-indiranagar-mg-road").json()
    assert any(f["properties"]["kind"] == "street_lamp" for f in fc["features"])
    page = client.get("/")
    assert page.status_code == 200 and "SafeHer" in page.text
    assert page.headers["cache-control"] == "no-cache"
