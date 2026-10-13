"""Foreground location sharing. Bearer capabilities, latest-point-only SQLite storage."""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

router = APIRouter(prefix="/api/trips", tags=["Live sharing"])
DB_FILE = Path(__file__).resolve().parents[1] / "var" / "trips.sqlite3"
TTL = 2 * 60 * 60


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    accuracy_m: float = Field(ge=0, le=100000, allow_inf_nan=False)


class TripRequest(BaseModel):
    # GeoJSON order: longitude, latitude. Only the selected route is shared.
    route: list[tuple[float, float]] = Field(default_factory=list, max_length=5000)

    @field_validator("route")
    @classmethod
    def validate_route(cls, route):
        if not all(math.isfinite(lon) and math.isfinite(lat) and
                   -180 <= lon <= 180 and -90 <= lat <= 90 for lon, lat in route):
            raise ValueError("Invalid route coordinates")
        return route


# Concurrency: WAL lets readers proceed while one process writes (the server runs several worker
# processes); the in-process lock keeps this process's threads from queueing on SQLite's busy
# handler. Privacy is unchanged: secure_delete overwrites deleted rows, and after any deletion the
# WAL is checkpointed and truncated so a stopped or expired location doesn't linger in the log.
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
    with closing(_connect()) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS trips (
            viewer TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, expires REAL NOT NULL,
            route TEXT NOT NULL, lat REAL, lon REAL, accuracy REAL, updated REAL)""")
    for f in (DB_FILE, DB_FILE.with_name(DB_FILE.name + "-wal"), DB_FILE.with_name(DB_FILE.name + "-shm")):
        if f.exists():
            f.chmod(0o600)
    _ready.add(key)


def _wipe_log(conn: sqlite3.Connection) -> None:
    """Fold the WAL into the database (where secure_delete overwrote deleted rows) and empty it."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


class _Tx:
    """Connection wrapper used inside `database()`; `deleted()` asks for a log wipe after commit."""

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
        tx = _Tx(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            tx.execute("DELETE FROM trips WHERE expires <= ?", (time.time(),))
            yield tx
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if tx.wipe:
            _wipe_log(conn)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def find(conn, token):
    row = conn.execute("SELECT * FROM trips WHERE viewer = ?", (digest(token),)).fetchone()
    if row is None:
        raise HTTPException(404, "Sharing stopped, expired, or link unavailable.")
    return row


def authorize(row, authorization):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(403, "Owner credential required.")
    if not secrets.compare_digest(row["owner_hash"], digest(authorization[7:])):
        raise HTTPException(403, "Owner credential required.")


@router.post("", status_code=201)
def create_trip(req: TripRequest, response: Response):
    viewer, owner = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expires = time.time() + TTL
    with database() as conn:
        if conn.execute("SELECT count(*) FROM trips").fetchone()[0] >= 1000:
            raise HTTPException(503, "Sharing capacity reached. Try again later.")
        conn.execute("INSERT INTO trips(viewer, owner_hash, expires, route) VALUES (?, ?, ?, ?)",
                     (digest(viewer), digest(owner), expires, json.dumps(req.route)))
    response.headers["Cache-Control"] = "no-store"
    return {"viewer_token": viewer, "owner_token": owner, "expires_at": expires}


@router.get("/{token}")
def read_trip(token: str, response: Response):
    with database() as conn:
        row = find(conn, token)
    response.headers["Cache-Control"] = "no-store"
    return {"expires_at": row["expires"], "route": json.loads(row["route"]),
            "location": None if row["updated"] is None else {
                "lat": row["lat"], "lon": row["lon"], "accuracy_m": row["accuracy"],
                "updated_at": row["updated"]}}


@router.put("/{token}/location", status_code=204)
def update_location(token: str, req: Location, authorization: str | None = Header(default=None)):
    with database() as conn:
        row = find(conn, token)
        authorize(row, authorization)
        conn.execute("UPDATE trips SET lat=?, lon=?, accuracy=?, updated=? WHERE viewer=?",
                     (req.lat, req.lon, req.accuracy_m, time.time(), digest(token)))


@router.delete("/{token}", status_code=204)
def stop_trip(token: str, authorization: str | None = Header(default=None)):
    with database() as conn:
        row = find(conn, token)
        authorize(row, authorization)
        conn.execute("DELETE FROM trips WHERE viewer=?", (digest(token),))


def purge_expired():
    """Called periodically, even if nobody opens an expired link."""
    with database():
        pass
