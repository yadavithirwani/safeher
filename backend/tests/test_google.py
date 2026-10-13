"""Google integration, tested against documented response shapes with a mock transport (no key, no network)."""

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from safeher import api, google
from safeher.areas import AREAS

DEL = AREAS["del-hauz-khas-saket"]
HAUZ_KHAS, SAKET = (28.5433, 77.2067), (28.5286, 77.2195)


def encode_polyline(points, precision=5):
    """Google's algorithm, for building fixtures."""
    out, prev = [], (0, 0)
    for lat, lon in points:
        cur = (round(lat * 10 ** precision), round(lon * 10 ** precision))
        for v in (cur[0] - prev[0], cur[1] - prev[1]):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev = cur
    return "".join(out)


class FakeGoogle:
    """Records requests and answers like the real APIs do."""

    def __init__(self, routes=None, status=200):
        self.requests: list[httpx.Request] = []
        self.routes = routes or []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "API key not valid"}})
        url = str(request.url)
        if "createSession" in url:
            return httpx.Response(200, json={"session": "sess-123", "expiry": "9999999999", "tileWidth": 256})
        if "/2dtiles/" in url:
            return httpx.Response(200, content=b"\x89PNG fake", headers={"content-type": "image/png"})
        if "/viewport" in url:
            return httpx.Response(200, json={"copyright": "Map data ©2026 Google"})
        if "places:autocomplete" in url:
            return httpx.Response(200, json={"suggestions": [
                {"placePrediction": {"placeId": "ChIJ_test_place_0001", "text": {"text": "Toit, Indiranagar"},
                                     "structuredFormat": {"mainText": {"text": "Toit"},
                                                          "secondaryText": {"text": "100 Feet Rd, Indiranagar"}}}},
                {"queryPrediction": {"text": {"text": "toit brewpub near me"}}},
            ]})
        if "/places/ChIJ" in url:
            pid = url.split("/places/")[1].split("?")[0]
            loc = {"latitude": 28.5450, "longitude": 77.2100} if pid.endswith("DEL") else {"latitude": 19.07, "longitude": 72.87}
            return httpx.Response(200, json={"id": pid, "displayName": {"text": "Some Place"},
                                             "formattedAddress": "Somewhere", "location": loc})
        if "places:searchNearby" in url:
            return httpx.Response(200, json={"places": [
                {"id": "p1", "displayName": {"text": "Hauz Khas Police Station"}, "primaryType": "police",
                 "location": {"latitude": 28.5440, "longitude": 77.2060}, "businessStatus": "OPERATIONAL",
                 "currentOpeningHours": {"openNow": True}},
                {"id": "p2", "displayName": {"text": "Closed Cafe"}, "primaryType": "cafe",
                 "location": {"latitude": 28.5445, "longitude": 77.2070}, "businessStatus": "OPERATIONAL",
                 "currentOpeningHours": {"openNow": False}},
                {"id": "p3", "displayName": {"text": "Gone Forever"}, "primaryType": "police",
                 "location": {"latitude": 28.5441, "longitude": 77.2061}, "businessStatus": "CLOSED_PERMANENTLY"},
                {"id": "p4", "displayName": {"text": "No Location"}},
            ]})
        if "computeRoutes" in url:
            return httpx.Response(200, json={"routes": [
                {"distanceMeters": 3000, "duration": "2250s", "routeLabels": ["DEFAULT_ROUTE"],
                 "polyline": {"encodedPolyline": encode_polyline(pts)}} for pts in self.routes]})
        if "streetview/metadata" in url:
            return httpx.Response(200, json={"status": "OK"})
        if "streetview" in url:
            return httpx.Response(200, content=b"\xff\xd8 jpeg", headers={"content-type": "image/jpeg"})
        return httpx.Response(404, json={"error": {"message": f"unexpected {url}"}})


def make_client(fake: FakeGoogle, key="test-key") -> google.GoogleClient:
    return google.GoogleClient(key=key, transport=httpx.MockTransport(fake))


# --- Polyline -------------------------------------------------------------------------------


def test_decode_googles_documented_example():
    assert google.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def test_polyline_roundtrip_and_truncation():
    pts = [(28.5433, 77.2067), (28.5401, 77.2101), (28.5286, 77.2195)]
    assert google.decode_polyline(encode_polyline(pts)) == pts
    with pytest.raises(google.GoogleError):
        google.decode_polyline(encode_polyline(pts)[:-1] + "_")


# --- Client: requests and parsing ---------------------------------------------------------


def test_key_is_sent_in_header_and_field_masks_are_set():
    fake = FakeGoogle()
    c = make_client(fake)
    c.place("ChIJ_test_place_DEL")
    c.nearby(28.54, 77.21, ["police"], 2500, 5)
    for r in fake.requests:
        assert r.headers["X-Goog-Api-Key"] == "test-key"
        assert "X-Goog-FieldMask" in r.headers
    assert "currentOpeningHours.openNow" in fake.requests[1].headers["X-Goog-FieldMask"]


def test_autocomplete_is_restricted_to_the_area_and_skips_query_predictions():
    fake = FakeGoogle()
    out = make_client(fake).autocomplete("toit", (DEL.south, DEL.west, DEL.north, DEL.east), "tok-1")
    body = json.loads(fake.requests[0].content)
    rect = body["locationRestriction"]["rectangle"]
    assert rect["low"] == {"latitude": DEL.south, "longitude": DEL.west}
    assert rect["high"] == {"latitude": DEL.north, "longitude": DEL.east}
    assert body["sessionToken"] == "tok-1" and body["includedRegionCodes"] == ["in"]
    assert out == [{"place_id": "ChIJ_test_place_0001", "main": "Toit", "secondary": "100 Feet Rd, Indiranagar"}]


def test_nearby_drops_closed_businesses_and_places_without_location():
    out = make_client(FakeGoogle()).nearby(28.544, 77.206, ["police"], 2500, 10)
    assert [p["name"] for p in out] == ["Hauz Khas Police Station", "Closed Cafe"]
    assert out[0]["open"] is True and out[1]["open"] is False


def test_nearby_radius_is_capped_at_googles_limit():
    fake = FakeGoogle()
    make_client(fake).nearby(28.54, 77.21, ["police"], 999_999, 50)
    body = json.loads(fake.requests[0].content)
    assert body["locationRestriction"]["circle"]["radius"] == 50000 and body["maxResultCount"] == 20


def test_walking_routes_are_decoded():
    pts = [HAUZ_KHAS, (28.5380, 77.2120), SAKET]
    out = make_client(FakeGoogle(routes=[pts])).walking_routes(HAUZ_KHAS, SAKET)
    assert out[0]["points"] == pts and out[0]["duration_s"] == 2250 and out[0]["distance_m"] == 3000


def test_tile_session_is_reused():
    fake = FakeGoogle()
    c = make_client(fake)
    c.tile("light", 14, 11700, 6800)
    c.tile("light", 14, 11701, 6800)
    assert sum("createSession" in str(r.url) for r in fake.requests) == 1


def test_dark_tiles_use_a_styled_session():
    fake = FakeGoogle()
    make_client(fake).tile("dark", 14, 1, 1)
    body = json.loads(next(r for r in fake.requests if "createSession" in str(r.url)).content)
    assert body["styles"] and body["mapType"] == "roadmap"


def test_errors_become_google_errors():
    with pytest.raises(google.GoogleError, match="403: API key not valid"):
        make_client(FakeGoogle(status=403)).place("ChIJ_test_place_DEL")

    def boom(request):
        raise httpx.ConnectTimeout("slow")
    with pytest.raises(google.GoogleError, match="network error"):
        google.GoogleClient(key="k", transport=httpx.MockTransport(boom)).place("ChIJ_test_place_DEL")
    with pytest.raises(google.GoogleError, match="not set"):
        google.GoogleClient(key="").place("ChIJ_test_place_DEL")


# --- API with Google switched on (fake client) ----------------------------------------------


@pytest.fixture
def google_on(monkeypatch, tmp_path):
    def setup(fake: FakeGoogle, features=("map_tiles", "autocomplete", "nearby", "routes", "streetview")):
        monkeypatch.setattr(google, "client", lambda: make_client(fake))
        monkeypatch.setattr(google, "feature", lambda name: name in features)
        monkeypatch.setattr(api, "REPORTS_FILE", tmp_path / "reports.jsonl")
        api._route_cache.clear()
        return TestClient(api.app)
    return setup


def plan_body(t="21:30"):
    return {"origin": {"lat": HAUZ_KHAS[0], "lon": HAUZ_KHAS[1]},
            "destination": {"lat": SAKET[0], "lon": SAKET[1]}, "depart_at": f"2026-10-14T{t}:00"}


def test_meta_reports_google_features(google_on):
    c = google_on(FakeGoogle())
    assert all(c.get("/api/meta").json()["google"].values())


def test_no_google_content_without_the_google_map(google_on):
    """Google's terms: Places/Routes content only on a Google map."""
    c = google_on(FakeGoogle(), features=("routes", "nearby", "autocomplete"))
    g = c.get("/api/meta").json()["google"]
    assert not any(g.values())


def test_google_route_identical_to_ours_is_folded_in(google_on):
    base = TestClient(api.app).post("/api/plan", json=plan_body()).json()
    ours = [tuple(p) for p in base["routes"][0]["geometry"]]
    c = google_on(FakeGoogle(routes=[ours]))
    p = c.post("/api/plan", json=plan_body()).json()
    assert len(p["routes"]) == len(base["routes"])
    assert any(r.get("also_google") for r in p["routes"])
    assert p["google_map"] is True


def test_different_google_route_is_scored_by_our_engine(google_on):
    base = TestClient(api.app).post("/api/plan", json=plan_body()).json()
    # A real street path that SafeHer didn't pick: our route reversed out and back isn't valid, so
    # take the alternative's geometry but shift it off our routes by detouring via a side street.
    from safeher.routing import load_area
    data = load_area(DEL.id)
    import networkx as nx
    s = min(data.graph.nodes, key=lambda n: abs(data.node_latlon(n)[0] - HAUZ_KHAS[0]) + abs(data.node_latlon(n)[1] - HAUZ_KHAS[1]))
    t = min(data.graph.nodes, key=lambda n: abs(data.node_latlon(n)[0] - SAKET[0]) + abs(data.node_latlon(n)[1] - SAKET[1]))
    via = min(data.graph.nodes, key=lambda n: abs(data.node_latlon(n)[0] - 28.5365) + abs(data.node_latlon(n)[1] - 77.2205))
    path = nx.shortest_path(data.graph, s, via, weight="length")[:-1] + nx.shortest_path(data.graph, via, t, weight="length")
    pts = [data.node_latlon(n) for n in path]
    c = google_on(FakeGoogle(routes=[pts]))
    p = c.post("/api/plan", json=plan_body()).json()
    g = [r for r in p["routes"] if r["source"] == "google"]
    assert len(g) == 1 and g[0]["label"] == "Google route"
    assert 0 <= g[0]["score"] <= 100 and g[0]["matched_share"] > 0.9  # follows mapped streets
    assert g[0]["plan_b"] is None and len(p["routes"]) == len(base["routes"]) + 1
    assert [r["score"] for r in p["routes"]] == sorted((r["score"] for r in p["routes"]), reverse=True)


def test_google_failure_keeps_safeher_routes(google_on):
    c = google_on(FakeGoogle(status=429))
    p = c.post("/api/plan", json=plan_body()).json()
    assert p["routes"] and all(r["source"] == "safeher" for r in p["routes"])
    assert any("Google routes unavailable" in n for n in p["data_notes"])


def test_google_route_leaving_the_area_is_not_scored(google_on):
    c = google_on(FakeGoogle(routes=[[HAUZ_KHAS, (28.60, 77.25), SAKET]]))
    p = c.post("/api/plan", json=plan_body()).json()
    assert all(r["source"] == "safeher" for r in p["routes"])
    assert any("leaves the demo area" in n for n in p["data_notes"])


def test_nearby_uses_live_google_hours_for_trips_leaving_now(google_on):
    c = google_on(FakeGoogle())
    now = datetime.now(timezone.utc).isoformat()
    r = c.get("/api/nearby", params={"lat": 28.5433, "lon": 77.2067, "kind": "police", "depart_at": now}).json()
    assert r["source"] == "google" and r["results"][0]["hours_source"] == "google"
    open_now = c.get("/api/nearby", params={"lat": 28.5433, "lon": 77.2067, "kind": "open_places",
                                            "depart_at": now}).json()
    assert all(x["open"] is True for x in open_now["results"])


def test_nearby_for_a_later_time_uses_open_data(google_on):
    c = google_on(FakeGoogle())
    r = c.get("/api/nearby", params={"lat": 28.5433, "lon": 77.2067, "kind": "police",
                                     "depart_at": "2026-10-14T23:45:00"}).json()
    assert r["source"] == "openstreetmap"


def test_nearby_falls_back_when_google_fails(google_on):
    c = google_on(FakeGoogle(status=500))
    now = datetime.now(timezone.utc).isoformat()
    r = c.get("/api/nearby", params={"lat": 28.5433, "lon": 77.2067, "kind": "police", "depart_at": now}).json()
    assert r["source"] == "openstreetmap" and r["results"]


def test_search_adds_google_predictions_after_local_results(google_on):
    c = google_on(FakeGoogle())
    r = c.get("/api/search", params={"q": "toit", "area": DEL.id, "session": "tok"}).json()
    g = [x for x in r if x.get("source") == "google"]
    assert g and g[0]["place_id"] and "Google" in g[0]["detail"]
    assert "lat" not in g[0]  # coordinates only come from Place Details when chosen


def test_place_outside_demo_areas_is_refused(google_on):
    c = google_on(FakeGoogle())
    assert c.get("/api/google/place/ChIJ_test_place_DEL").json()["area"] == DEL.id
    r = c.get("/api/google/place/ChIJ_test_place_MUM")
    assert r.status_code == 422 and "outside the demo areas" in r.json()["detail"]


def test_tiles_and_images_are_proxied_without_caching(google_on):
    c = google_on(FakeGoogle())
    t = c.get("/api/google/tiles/dark/14/11700/6800")
    assert t.status_code == 200 and t.headers["cache-control"] == "no-store" and t.content.startswith(b"\x89PNG")
    sv = c.get("/api/google/streetview", params={"lat": 28.54, "lon": 77.21, "heading": 90})
    assert sv.status_code == 200 and sv.headers["cache-control"] == "no-store"
    assert c.get("/api/google/tiles/light/3/9/0").status_code == 404  # x out of range at z=3


def test_endpoints_are_off_without_a_key(monkeypatch):
    monkeypatch.setattr(google, "api_key", lambda: "")
    c = TestClient(api.app)
    assert c.get("/api/google/tiles/light/1/0/0").status_code == 404
    assert c.get("/api/google/place/ChIJ_test_place_DEL").status_code == 404
    assert not any(c.get("/api/meta").json()["google"].values())
