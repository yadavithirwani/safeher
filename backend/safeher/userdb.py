"""User data: live-sharing trips and community reports, in one SQLite file (backend/var/safeher.sqlite3).

Reference data (streets, places, night lights, NCRB) is read-only in data/safeher.duckdb; anything people
create at runtime lives here, because SQLite takes concurrent writes from several worker processes.

Concurrency: WAL lets readers proceed while one process writes; the in-process lock keeps this
process's threads from queueing on SQLite's busy handler. Privacy: secure_delete overwrites deleted
rows, and after any deletion the WAL is checkpointed and truncated so deleted data doesn't linger.
"""

from __future__ import annotations

import fcntl
import sqlite3
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
