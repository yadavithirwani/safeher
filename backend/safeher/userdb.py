"""User data: live-sharing trips and community reports.

Two backends with the same operations (the functions at the end of this file):
  - SQLite (default): one file, backend/var/safeher.sqlite3. Local development and single servers.
  - Firestore (SAFEHER_USERDB=firestore): for Cloud Run, whose disk is wiped when it scales to zero.

Reference data (streets, places, night lights, NCRB) is read-only in data/safeher.duckdb; anything people
create at runtime lives here, because SQLite takes concurrent writes from several worker processes.

Concurrency: WAL lets readers proceed while one process writes; the in-process lock keeps this
process's threads from queueing on SQLite's busy handler. Privacy: secure_delete overwrites deleted
rows, and after any deletion the WAL is checkpointed and truncated so deleted data doesn't linger.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import time
import threading
from contextlib import closing, contextmanager
from pathlib import Path

DB_FILE = Path(__file__).resolve().parents[1] / "var" / "safeher.sqlite3"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS trips (
        viewer TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, expires REAL NOT NULL,
        route TEXT NOT NULL, lat REAL, lon REAL, accuracy REAL, updated REAL)""",
    """CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, city TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
        category TEXT NOT NULL, created_at TEXT NOT NULL, verification TEXT NOT NULL, note TEXT)""",
)

_DB_LOCK = threading.Lock()
_ready: set[str] = set()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=10, isolation_level=None)  # autocommit; we BEGIN explicitly
    conn.execute("PRAGMA secure_delete=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def _setup() -> None:
    key = str(DB_FILE)
    if key in _ready:
        return
    DB_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Switching to WAL needs an exclusive lock and SQLite doesn't wait for it, so worker processes
    # starting together take turns via a lock file (the thread lock only covers this process).
    with open(DB_FILE.with_name(DB_FILE.name + ".setup-lock"), "a") as lock, closing(_connect()) as conn:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            conn.execute("PRAGMA journal_mode=WAL")
        for sql in SCHEMA:
            conn.execute(sql)
    for f in (DB_FILE, DB_FILE.with_name(DB_FILE.name + "-wal"), DB_FILE.with_name(DB_FILE.name + "-shm")):
        if f.exists():
            f.chmod(0o600)
    _ready.add(key)


class Tx:
    """Connection wrapper used inside `database()`; a DELETE that removed rows asks for a log wipe."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn, self.wipe = conn, False

    def execute(self, *args):
        cur = self.conn.execute(*args)
        if str(args[0]).lstrip().upper().startswith("DELETE") and cur.rowcount > 0:
            self.wipe = True
        return cur


@contextmanager
def database():
    with _DB_LOCK:  # setup too: two threads switching a new file to WAL at once would collide
        _setup()
    with _DB_LOCK, closing(_connect()) as conn:
        tx = Tx(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield tx
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if tx.wipe:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # fold the WAL in and empty it


# --- Operations used by the API (same behaviour on both backends) ---------------------------------
#
# Reports: rows {id, city, lat, lon, category, created_at, verification, note}.
# Trips: rows {viewer, owner_hash, expires, route (JSON text), lat, lon, accuracy, updated}; keyed by
# the hash of the viewer token. Expired trips are removed before every trip operation.

TRIP_CAPACITY = 1000


def _firestore() -> bool:
    return os.environ.get("SAFEHER_USERDB", "sqlite").strip().lower() == "firestore"


def reports_version() -> tuple:
    """Changes whenever a report is added (cheap: one query or one document read)."""
    if _firestore():
        return _fs.reports_version()
    with database() as db:
        n, last = db.execute("SELECT count(*), max(id) FROM reports").fetchone()
    return (str(DB_FILE), n, last)


def list_reports(city: str | None = None) -> list[dict]:
    if _firestore():
        return _fs.list_reports(city)
    with database() as db:
        rows = db.execute("SELECT * FROM reports" + (" WHERE city = ?" if city else "") + " ORDER BY id",
                          (city,) if city else ()).fetchall()
    return [dict(r) for r in rows]


def add_report(row: dict) -> int:
    if _firestore():
        return _fs.add_report(row)
    with database() as db:
        return db.execute("INSERT INTO reports(city, lat, lon, category, created_at, verification, note) "
                          "VALUES (:city, :lat, :lon, :category, :created_at, :verification, :note)", row).lastrowid


def trip_get(viewer: str) -> dict | None:
    if _firestore():
        return _fs.trip_get(viewer)
    with _trip_tx() as db:
        row = db.execute("SELECT * FROM trips WHERE viewer = ?", (viewer,)).fetchone()
    return dict(row) if row else None


def trip_create(viewer: str, owner_hash: str, expires: float, route: list) -> bool:
    """False when TRIP_CAPACITY live trips already exist."""
    if _firestore():
        return _fs.trip_create(viewer, owner_hash, expires, route)
    with _trip_tx() as db:
        if db.execute("SELECT count(*) FROM trips").fetchone()[0] >= TRIP_CAPACITY:
            return False
        db.execute("INSERT INTO trips(viewer, owner_hash, expires, route) VALUES (?, ?, ?, ?)",
                   (viewer, owner_hash, expires, json.dumps(route)))
    return True


def trip_set_location(viewer: str, lat: float, lon: float, accuracy: float) -> None:
    if _firestore():
        return _fs.trip_set_location(viewer, lat, lon, accuracy)
    with _trip_tx() as db:
        db.execute("UPDATE trips SET lat=?, lon=?, accuracy=?, updated=? WHERE viewer=?",
                   (lat, lon, accuracy, time.time(), viewer))


def trip_delete(viewer: str) -> None:
    if _firestore():
        return _fs.trip_delete(viewer)
    with _trip_tx() as db:
        db.execute("DELETE FROM trips WHERE viewer=?", (viewer,))


def purge_expired_trips() -> None:
    if _firestore():
        return _fs.purge_expired()
    with _trip_tx():
        pass


@contextmanager
def _trip_tx():
    """A transaction that first drops expired trips."""
    with database() as tx:
        tx.execute("DELETE FROM trips WHERE expires <= ?", (time.time(),))
        yield tx


class _FirestoreBackend:
    """Reports and trips in Firestore (default database). Trips carry `expire_at` for a TTL policy, so
    Firestore also deletes expired ones by itself; they are filtered by `expires` on every read."""

    def __init__(self):
        self._client = None
        self._lock = threading.Lock()

    @property
    def db(self):
        with self._lock:
            if self._client is None:
                from google.cloud import firestore
                self._client = firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None)
            return self._client

    # reports ----------------------------------------------------------------------------------
    def reports_version(self) -> tuple:
        snap = self.db.collection("meta").document("reports").get()
        data = snap.to_dict() if snap.exists else {}
        return ("firestore", data.get("count", 0), data.get("last_id", 0))

    def list_reports(self, city):
        q = self.db.collection("reports")
        if city:
            q = q.where("city", "==", city)
        return sorted((d.to_dict() for d in q.stream()), key=lambda r: r["id"])

    def add_report(self, row) -> int:
        from google.cloud import firestore
        counter = self.db.collection("meta").document("reports")

        @firestore.transactional
        def write(tx):
            snap = counter.get(transaction=tx)
            data = snap.to_dict() if snap.exists else {}
            new_id = int(data.get("last_id", 0)) + 1
            tx.set(counter, {"last_id": new_id, "count": int(data.get("count", 0)) + 1})
            tx.set(self.db.collection("reports").document(str(new_id)), {**row, "id": new_id})
            return new_id
        return write(self.db.transaction())

    # trips ------------------------------------------------------------------------------------
    def _trip(self, viewer):
        return self.db.collection("trips").document(viewer)

    def trip_get(self, viewer):
        snap = self._trip(viewer).get()
        if not snap.exists:
            return None
        row = snap.to_dict()
        if row["expires"] <= time.time():
            self._trip(viewer).delete()
            return None
        return {"viewer": viewer, "lat": None, "lon": None, "accuracy": None, "updated": None,
                **{k: v for k, v in row.items() if k != "expire_at"}}

    def trip_create(self, viewer, owner_hash, expires, route) -> bool:
        from datetime import datetime, timezone
        self.purge_expired()
        live = self.db.collection("trips").count().get()[0][0].value
        if live >= TRIP_CAPACITY:
            return False
        self._trip(viewer).set({"owner_hash": owner_hash, "expires": expires, "route": json.dumps(route),
                                "expire_at": datetime.fromtimestamp(expires, timezone.utc)})
        return True

    def trip_set_location(self, viewer, lat, lon, accuracy):
        self._trip(viewer).update({"lat": lat, "lon": lon, "accuracy": accuracy, "updated": time.time()})

    def trip_delete(self, viewer):
        self._trip(viewer).delete()

    def purge_expired(self):
        for snap in self.db.collection("trips").where("expires", "<=", time.time()).limit(200).stream():
            snap.reference.delete()


_fs = _FirestoreBackend()
