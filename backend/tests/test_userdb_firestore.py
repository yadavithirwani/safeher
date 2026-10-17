"""The Firestore user-data backend (SAFEHER_USERDB=firestore, used on Cloud Run), against an
in-memory stand-in for the parts of google-cloud-firestore the app uses. Same API flows as SQLite."""
import time

import pytest
from fastapi.testclient import TestClient

from safeher import api, userdb


class Snap:
    def __init__(self, ref, data):
        self.reference, self._data = ref, data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class Doc:
    def __init__(self, coll, doc_id):
        self.coll, self.id = coll, doc_id

    def get(self, transaction=None):
        return Snap(self, self.coll.docs.get(self.id))

    def set(self, data):
        self.coll.docs[self.id] = dict(data)

    def update(self, data):
        if self.id not in self.coll.docs:
            raise KeyError("No document to update")  # Firestore raises NotFound
        self.coll.docs[self.id].update(data)

    def delete(self):
        self.coll.docs.pop(self.id, None)


class Query:
    OPS = {"==": lambda a, b: a == b, "<=": lambda a, b: a <= b}

    def __init__(self, coll, filters=(), limit=None):
        self.coll, self.filters, self._limit = coll, list(filters), limit

    def where(self, field, op, value):
        return Query(self.coll, self.filters + [(field, op, value)], self._limit)

    def limit(self, n):
        return Query(self.coll, self.filters, n)

    def stream(self):
        out = [Snap(Doc(self.coll, k), v) for k, v in list(self.coll.docs.items())
               if all(f in v and self.OPS[op](v[f], val) for f, op, val in self.filters)]
        return out[: self._limit] if self._limit else out


class Count:
    def __init__(self, n):
        self.value = n


class Coll(Query):
    def __init__(self):
        self.docs = {}
        super().__init__(self)

    def document(self, doc_id):
        return Doc(self, doc_id)

    def count(self):
        coll = self

        class Agg:
            def get(self):
                return [[Count(len(coll.docs))]]
        return Agg()


class Tx:
    def set(self, ref, data):
        ref.set(data)


class FakeFirestore:
    def __init__(self):
        self.colls = {}

    def collection(self, name):
        return self.colls.setdefault(name, Coll())

    def transaction(self):
        return Tx()


@pytest.fixture
def firestore(monkeypatch):
    from google.cloud import firestore as real
    fake = FakeFirestore()
    monkeypatch.setenv("SAFEHER_USERDB", "firestore")
    monkeypatch.setattr(userdb._fs, "_client", fake)
    monkeypatch.setattr(real, "transactional", lambda fn: fn)  # the stand-in runs writes directly
    monkeypatch.setattr(api, "_reports_cache", ((), []))
    return fake


@pytest.fixture
def client():
    return TestClient(api.app)


def test_reports_are_stored_counted_and_listed(firestore, client):
    v0 = userdb.reports_version()
    r = client.post("/api/reports", json={"lat": 28.5433, "lon": 77.2067, "category": "broken_light"})
    assert r.status_code == 201 and r.json()["id"] == 1
    client.post("/api/reports", json={"lat": 12.9784, "lon": 77.6408, "category": "harassment"})
    assert userdb.reports_version() == ("firestore", 2, 2) != v0
    assert [x["category"] for x in client.get("/api/reports").json()] == ["broken_light", "harassment"]
    assert [x["id"] for x in client.get("/api/reports", params={"city": "bengaluru"}).json()] == [2]
    assert "note" not in client.get("/api/reports").json()[0]
    assert len(api._reports_for_planning()) == 2  # the planner sees them


def test_live_trip_lifecycle(firestore, client):
    t = client.post("/api/trips", json={"route": [[77.2067, 28.5433], [77.2195, 28.5286]]}).json()
    doc = firestore.collection("trips").docs[userdb_digest(t["viewer_token"])]
    assert "expire_at" in doc and doc["route"]  # TTL field for Firestore's own clean-up
    assert client.get(f"/api/trips/{t['viewer_token']}").json()["location"] is None
    auth = {"Authorization": f"Bearer {t['owner_token']}"}
    assert client.put(f"/api/trips/{t['viewer_token']}/location", json={"lat": 28.54, "lon": 77.21, "accuracy_m": 8},
                      headers=auth).status_code == 204
    loc = client.get(f"/api/trips/{t['viewer_token']}").json()["location"]
    assert loc["lat"] == 28.54 and loc["accuracy_m"] == 8
    bad = {"Authorization": f"Bearer {t['viewer_token']}"}
    assert client.put(f"/api/trips/{t['viewer_token']}/location", json={"lat": 1, "lon": 1, "accuracy_m": 5},
                      headers=bad).status_code == 403
    assert client.delete(f"/api/trips/{t['viewer_token']}", headers=auth).status_code == 204
    assert client.get(f"/api/trips/{t['viewer_token']}").status_code == 404
    assert not firestore.collection("trips").docs  # stopping deletes the stored position


def test_expired_trips_are_gone_and_purged(firestore, client):
    t = client.post("/api/trips", json={"route": []}).json()
    trips = firestore.collection("trips").docs
    trips[userdb_digest(t["viewer_token"])]["expires"] = time.time() - 1
    assert client.get(f"/api/trips/{t['viewer_token']}").status_code == 404
    assert not trips
    t2 = client.post("/api/trips", json={"route": []}).json()
    trips[userdb_digest(t2["viewer_token"])]["expires"] = time.time() - 1
    userdb.purge_expired_trips()
    assert not trips


def test_capacity_is_enforced(firestore, client, monkeypatch):
    monkeypatch.setattr(userdb, "TRIP_CAPACITY", 2)
    assert client.post("/api/trips", json={"route": []}).status_code == 201
    assert client.post("/api/trips", json={"route": []}).status_code == 201
    assert client.post("/api/trips", json={"route": []}).status_code == 503


def userdb_digest(token):
    from safeher.trips import digest
    return digest(token)
