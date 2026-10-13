"""Isolated live-sharing tests: no map snapshots, network, or actual GPS."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from safeher import api, trips


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(trips, "DB_FILE", tmp_path / "trips.sqlite3")
    with TestClient(api.app) as client:
        yield client


def create(client):
    response = client.post("/api/trips", json={"route": [[77.21, 28.54], [77.22, 28.53]]})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["owner_token"] != result["viewer_token"]
    return result, f'/api/trips/{result["viewer_token"]}'


def owner(session):
    return {"Authorization": f'Bearer {session["owner_token"]}'}


def test_share_update_read_and_stop(client):
    session, path = create(client)
    assert client.get(path).json()["location"] is None
    point = {"lat": 28.54, "lon": 77.21, "accuracy_m": 12}
    assert client.put(path + "/location", json=point, headers=owner(session)).status_code == 204
    result = client.get(path)
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["referrer-policy"] == "no-referrer"
    assert result.json()["location"]["lat"] == point["lat"]
    assert result.json()["location"]["updated_at"] > 0
    assert "owner_token" not in result.json()
    assert client.delete(path, headers=owner(session)).status_code == 204
    assert client.get(path).status_code == 404
    assert client.put(path + "/location", json=point, headers=owner(session)).status_code == 404
    with sqlite3.connect(trips.DB_FILE) as conn:
        assert conn.execute("SELECT count(*) FROM trips").fetchone()[0] == 0


def test_viewer_cannot_update_or_stop(client):
    session, path = create(client)
    point = {"lat": 28.54, "lon": 77.21, "accuracy_m": 10}
    for headers in ({}, {"Authorization": f'Bearer {session["viewer_token"]}'},
                    {"Authorization": "Bearer wrong"}):
        assert client.put(path + "/location", json=point, headers=headers).status_code == 403
        assert client.delete(path, headers=headers).status_code == 403
    other, _ = create(client)
    assert client.delete(path, headers=owner(other)).status_code == 403
    assert client.get("/api/trips/not-a-token").status_code == 404


def test_expiry_deletes_data(client, monkeypatch):
    session, path = create(client)
    monkeypatch.setattr(trips.time, "time", lambda: session["expires_at"] + 1)
    trips.purge_expired()
    assert client.get(path).status_code == 404
    with sqlite3.connect(trips.DB_FILE) as conn:
        assert conn.execute("SELECT count(*) FROM trips").fetchone()[0] == 0


def test_latest_only_and_hashed_credentials(client):
    session, path = create(client)
    for lat in (28.54, 28.55):
        assert client.put(path + "/location", headers=owner(session),
                          json={"lat": lat, "lon": 77.21, "accuracy_m": 1}).status_code == 204
    assert client.get(path).json()["location"]["lat"] == 28.55
    with sqlite3.connect(trips.DB_FILE) as conn:
        rows = conn.execute("SELECT * FROM trips").fetchall()
        assert len(rows) == 1
        assert session["viewer_token"] not in str(rows)
        assert session["owner_token"] not in str(rows)


@pytest.mark.parametrize("point", [
    {"lat": 91, "lon": 77, "accuracy_m": 1},
    {"lat": 28, "lon": 181, "accuracy_m": 1},
    {"lat": 28, "lon": 77, "accuracy_m": -1},
    {"lat": "NaN", "lon": 77, "accuracy_m": 1},
])
def test_bad_location_rejected(client, point):
    session, path = create(client)
    assert client.put(path + "/location", headers=owner(session), json=point).status_code == 422


def test_route_validation_and_viewer_page(client):
    assert client.post("/api/trips", json={"route": [[181, 28]]}).status_code == 422
    assert client.post("/api/trips", json={"route": [[77, 91]]}).status_code == 422
    assert client.post("/api/trips", json={"route": [["NaN", 28]]}).status_code == 422
    page = client.get("/live")
    assert page.status_code == 200
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["cache-control"] == "no-store"
    assert "Foreground-only" in page.text
