"""Go anywhere: trips outside the configured areas download street data on first use."""

import json
import shutil
import threading
import time

import pytest
from fastapi.testclient import TestClient

from safeher import api, areas, prepare
from safeher.routing import load_area
import httpx

from safeher import google
from tests.test_google import FakeGoogle

DEL = areas.AREAS["del-hauz-khas-saket"]
HAUZ_KHAS, SAKET = (28.5433, 77.2067), (28.5286, 77.2195)
ORIGINAL_DOWNLOAD = prepare._download  # captured at import, before conftest stubs it per test
from safeher import citydata as _citydata  # noqa: E402
REAL_CITY_FOR_BBOX = _citydata.city_for_bbox
pytestmark = pytest.mark.skipif(not (DEL.dir / "walk.graphml").exists(), reason="OSM snapshots not fetched")


def fake_save_area(area, write_meta=True):
    """Stand-in for the OpenStreetMap download: copies the cached Delhi snapshot."""
    area.dir.mkdir(parents=True, exist_ok=True)
    for f in ("walk.graphml", "pois.json"):
        shutil.copy(DEL.dir / f, area.dir / f)
    meta = {"area": area.id, "nodes": 1, "edges": 1, "pois": 1}
    if write_meta:
        (area.dir / "meta.json").write_text(json.dumps(meta))
    return meta


@pytest.fixture
def anywhere(monkeypatch):
    """Pretend no areas are configured, so Delhi points need a trip-area download."""
    from safeher import osm
    monkeypatch.setattr(areas, "AREAS", {})
    monkeypatch.setattr(osm, "save_area", fake_save_area)
    monkeypatch.setattr(prepare, "_download", ORIGINAL_DOWNLOAD)  # real download logic, fake data source
    from safeher import citydata
    monkeypatch.setattr(citydata, "city_for_bbox", lambda *a: None)  # exercise the Overpass path by default
    load_area.cache_clear()
    api._plan_cache.clear()
    yield TestClient(api.app)
    prepare.wait_idle()  # don't let a background download spill into the next test's folder
    load_area.cache_clear()




def body(o=HAUZ_KHAS, d=SAKET):
    return {"origin": {"lat": o[0], "lon": o[1]}, "destination": {"lat": d[0], "lon": d[1]},
            "depart_at": "2026-10-16T21:30:00", "trip_name": "Hauz Khas → Saket"}


def wait_ready(client, area_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/api/prepare/{area_id}").json()
        if st["state"] in ("ready", "failed"):
            return st
        time.sleep(0.2)
    raise AssertionError("download never finished")


def test_first_plan_prepares_then_plans(anywhere):
    r = anywhere.post("/api/plan", json=body())
    assert r.status_code == 202
    job = r.json()["job"]
    assert job["area"].startswith("trip-") and job["state"] in ("queued", "downloading", "checking", "ready")
    assert wait_ready(anywhere, job["area"])["state"] == "ready"
    p = anywhere.post("/api/plan", json=body())
    assert p.status_code == 200
    assert p.json()["area"]["id"] == job["area"] and p.json()["area"]["name"] == "Hauz Khas → Saket"
    assert len(p.json()["routes"]) >= 2
    e = anywhere.post("/api/explain", json=body())
    assert e.status_code == 200 and e.json()["checked"]


def test_nearby_trip_reuses_the_downloaded_area(anywhere):
    first = anywhere.post("/api/plan", json=body()).json()["job"]["area"]
    wait_ready(anywhere, first)
    # A slightly different trip inside the same corridor: instant, no new download.
    r = anywhere.post("/api/plan", json=body((28.5420, 77.2075), (28.5300, 77.2180)))
    assert r.status_code == 200 and r.json()["area"]["id"] == first


def test_only_one_download_per_corridor_even_when_asked_concurrently(anywhere, monkeypatch):
    calls = []
    def slow_save(area, write_meta=True):
        calls.append(area.id)
        time.sleep(0.5)
        return fake_save_area(area, write_meta)
    from safeher import osm
    monkeypatch.setattr(osm, "save_area", slow_save)
    threads = [threading.Thread(target=lambda: anywhere.post("/api/plan", json=body())) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    area_id = anywhere.post("/api/plan", json=body()).json().get("job", {}).get("area") or \
        anywhere.post("/api/plan", json=body()).json()["area"]["id"]
    wait_ready(anywhere, area_id)
    assert len(calls) == 1


def test_failed_download_is_reported_and_not_retried_immediately(anywhere, monkeypatch):
    from safeher import osm
    def boom(area, write_meta=True):
        raise RuntimeError("all Overpass mirrors failed")
    monkeypatch.setattr(osm, "save_area", boom)
    job = anywhere.post("/api/plan", json=body()).json()["job"]
    st = wait_ready(anywhere, job["area"])
    assert st["state"] == "failed" and "Overpass" in st["message"]
    again = anywhere.post("/api/plan", json=body())
    assert again.status_code == 202 and again.json()["job"]["state"] == "failed"  # retry pause


def test_tiny_or_fragmented_download_is_rejected(anywhere, monkeypatch):
    monkeypatch.setitem(prepare._A, "min_nodes", 10_000_000)
    job = anywhere.post("/api/plan", json=body()).json()["job"]
    st = wait_ready(anywhere, job["area"])
    assert st["state"] == "failed" and "street points" in st["message"]
    assert not (areas.TRIP_DATA_DIR / job["area"] / "meta.json").exists()


def test_too_far_is_refused_before_downloading(anywhere):
    r = anywhere.post("/api/plan", json=body(HAUZ_KHAS, (28.70, 77.30)))
    assert r.status_code == 422 and "km" in r.json()["detail"]


def test_status_for_unknown_or_malformed_ids(anywhere):
    assert anywhere.get("/api/prepare/trip-0000000000").json()["state"] == "unknown"
    assert anywhere.get("/api/prepare/../../etc").status_code == 404


def test_corridor_contains_both_points_with_margin():
    o, d = (12.9795, 77.6407), (12.9352, 77.6245)
    south, west, north, east = prepare.corridor(o, d)
    for lat, lon in (o, d):
        assert south < lat < north and west < lon < east
    assert (north - max(o[0], d[0])) * 111_320 >= prepare.MARGIN_M - 120


def test_nearby_outside_any_area_without_google_says_so(anywhere):
    r = anywhere.get("/api/nearby", params={"lat": 19.07, "lon": 72.87, "kind": "police"})
    assert r.status_code == 422 and "no street data here" in r.json()["detail"]


def test_preview_shows_googles_route_while_preparing(anywhere, monkeypatch):
    """Found in the browser with a real key: the preview crashed (missing import); tests had Google off."""
    fake = FakeGoogle(routes=[[HAUZ_KHAS, (28.538, 77.212), SAKET]])
    monkeypatch.setattr(google, "client", lambda: google.GoogleClient(key="k", transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(google, "feature", lambda name: True)
    api._route_cache.clear()
    r = anywhere.post("/api/plan", json=body())
    assert r.status_code == 202
    preview = r.json()["preview"]
    assert len(preview) == 1 and preview[0]["label"] == "Google route" and preview[0]["duration_min"] > 0
    assert preview[0]["geometry"][0] == [HAUZ_KHAS[0], HAUZ_KHAS[1]]


def test_corridor_inside_a_built_city_comes_from_the_local_database(anywhere, monkeypatch):
    """No network: Delhi trips are cut from data/cities/delhi.duckdb when it exists."""
    from safeher import citydata, osm
    if not citydata.db_path("delhi").exists():
        pytest.skip("Delhi city database not built (python -m safeher.citydata build delhi)")
    monkeypatch.setattr(citydata, "city_for_bbox", REAL_CITY_FOR_BBOX)
    def must_not_download(*a, **k):
        raise AssertionError("downloaded from Overpass although the city database covers this trip")
    monkeypatch.setattr(osm, "save_area", must_not_download)
    job = anywhere.post("/api/plan", json=body()).json()["job"]
    st = wait_ready(anywhere, job["area"])
    assert st["state"] == "ready", st["message"]
    p = anywhere.post("/api/plan", json=body()).json()
    assert [r["distance_m"] for r in sorted(p["routes"], key=lambda r: r["distance_m"])][0] == 2838  # same streets as before


def test_status_writes_are_safe_under_concurrency(tmp_path):
    """Found by testing: a shared temp file made concurrent status updates fail."""
    a = prepare.trip_area(HAUZ_KHAS, SAKET, "race")
    errors = []

    def writer(i):
        try:
            for _ in range(50):
                prepare._write_status(a, "downloading", f"writer {i}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert json.loads((a.dir / "status.json").read_text())["state"] == "downloading"
    assert not list(a.dir.glob("*.tmp"))
