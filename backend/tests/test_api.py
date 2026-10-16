import pytest
from fastapi.testclient import TestClient

from safeher import api, store
from safeher.cities import CITIES

pytestmark = pytest.mark.skipif(not store.available(), reason="run: python -m safeher.data build")

HAUZ_KHAS, SAKET = {"lat": 28.5433, "lon": 77.2067}, {"lat": 28.5286, "lon": 77.2195}


@pytest.fixture
def client():
    return TestClient(api.app)


def test_cities_have_landmarks(client):
    cities = client.get("/api/cities").json()
    assert {c["id"] for c in cities} == set(CITIES)
    assert all(len(c["landmarks"]) >= 2 for c in cities)
    meta = client.get("/api/meta").json()
    assert meta["default_city"] in CITIES and "default_area" not in meta and "anywhere" not in meta


def test_plan_returns_routes_with_pieces(client):
    r = client.post("/api/plan", json={"origin": HAUZ_KHAS, "destination": SAKET, "depart_at": "2026-10-13T21:30:00"})
    assert r.status_code == 200
    body = r.json()
    assert body["departure_local"].endswith("+05:30")  # naive time read as local area time
    route = body["routes"][0]
    assert route["pieces"] and all("score" in p and "light" in p for p in route["pieces"])
    for w in route["weak_stretches"]:  # endpoints match piece coordinates so the UI can highlight them
        assert any(p["coords"][0] == w["start"] for p in route["pieces"])


def test_plan_outside_supported_cities_is_refused_clearly(client):
    r = client.post("/api/plan", json={"origin": {"lat": 19.07, "lon": 72.87}, "destination": {"lat": 19.08, "lon": 72.88}})
    assert r.status_code == 422 and "Delhi and Bengaluru only" in r.json()["detail"]
    assert client.get("/api/prepare/trip-0123456789").status_code == 404  # the download queue is gone


def test_plan_across_two_cities_is_refused(client):
    r = client.post("/api/plan", json={"origin": HAUZ_KHAS, "destination": {"lat": 12.9784, "lon": 77.6408}})
    assert r.status_code == 422 and "same city" in r.json()["detail"]


def test_plan_too_far_to_walk_is_422(client):
    r = client.post("/api/plan", json={"origin": HAUZ_KHAS, "destination": {"lat": 28.70, "lon": 77.30}})
    assert r.status_code == 422 and "plans walks up to" in r.json()["detail"]


def test_any_trip_inside_a_city_plans_without_downloading(client):
    """Not just the landmarks: two points across town in Delhi (Connaught Place → India Gate)."""
    r = client.post("/api/plan", json={"origin": {"lat": 28.6315, "lon": 77.2167},
                                       "destination": {"lat": 28.6129, "lon": 77.2295}, "depart_at": "2026-10-13T21:30:00"})
    assert r.status_code == 200 and r.json()["routes"] and r.json()["area"]["city"] == "delhi"


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
    assert len(client.get("/api/reports", params={"city": "delhi"}).json()) == 1
    assert client.get("/api/reports", params={"city": "bengaluru"}).json() == []


def test_report_validation(client):
    assert client.post("/api/reports", json={"lat": 28.54, "lon": 77.21, "category": "spam"}).status_code == 422
    assert client.post("/api/reports", json={"lat": 19.0, "lon": 72.8, "category": "harassment"}).status_code == 422


def test_layers_and_web_app(client):
    box = {"south": 12.965, "west": 77.60, "north": 12.985, "east": 77.645}  # Indiranagar → MG Road
    fc = client.get("/api/layers", params=box).json()
    kinds = {f["properties"]["kind"] for f in fc["features"]}
    assert {"street_lamp", "lit_street", "night_light"} <= kinds
    points = [f for f in fc["features"] if f["geometry"]["type"] == "Point"]
    assert all(box["west"] <= f["geometry"]["coordinates"][0] <= box["east"] for f in points)
    pixels = [f["properties"] for f in fc["features"] if f["properties"]["kind"] == "night_light"]
    assert all(0 <= p["rank"] <= 1 and p["radiance"] > 0 for p in pixels)
    assert max(p["rank"] for p in pixels) > 0.9  # central Bengaluru is among the city's brightest areas
    whole_city = {"south": 12.83, "west": 77.45, "north": 13.15, "east": 77.78}
    assert client.get("/api/layers", params=whole_city).status_code == 422  # zoom in first
    page = client.get("/")
    assert page.status_code == 200 and "SafeHer" in page.text
    assert page.headers["cache-control"] == "no-cache"


def test_night_lights_mark_dark_places_dark():
    """NASA night lights rank Hauz Khas Deer Park (woodland) below the metro station and Select Citywalk mall."""
    from safeher import store
    from safeher.cities import CITIES
    rank = lambda lat, lon: min(store.night_light_pixels(CITIES["delhi"], lat, lon, lat, lon),  # noqa: E731
                                key=lambda p: abs(p["lat"] - lat) + abs(p["lon"] - lon))["rank"]
    assert rank(28.5525, 77.1950) < rank(28.5433, 77.2067) < rank(28.5286, 77.2195)  # park < metro < mall


def test_explain_endpoint_returns_checked_text(client, monkeypatch):
    monkeypatch.setenv("SAFEHER_LLM", "template")  # no network or API key needed
    body = {"origin": HAUZ_KHAS, "destination": SAKET, "depart_at": "2026-10-13T21:30:00"}
    routes = client.post("/api/plan", json=body).json()["routes"]
    best, shortest = routes[0], next(r for r in routes if r["label"] == "Shortest")
    for lang in ("en", "hi"):
        r = client.post("/api/explain", json={**body, "lang": lang})
        assert r.status_code == 200
        data = r.json()
        assert data["provider"] == "template" and data["checked"] and data["lang"] == lang
        assert str(best["score"]) in data["text"] and str(shortest["score"]) in data["text"]
    assert client.post("/api/explain", json={**body, "lang": "fr"}).status_code == 422


def test_report_far_from_any_street_is_refused(client):
    """Found by fuzzing: a tap far from streets used to crash the server. (Turahalli forest, >300 m from any street.)"""
    r = client.post("/api/reports", json={"lat": 12.8625, "lon": 77.4945, "category": "harassment"})
    assert r.status_code == 422 and "near a street" in r.json()["detail"]


def test_nearby_where_there_are_no_streets_is_a_clear_error_not_a_crash(client):
    """Found by fuzzing: inside Delhi's bbox but nowhere near a mapped street."""
    r = client.get("/api/nearby", params={"lat": 28.75, "lon": 77.3125, "kind": "police", "depart_at": "2026-10-14T23:45:00"})
    assert r.status_code == 422 and "No street data" in r.json()["detail"]
