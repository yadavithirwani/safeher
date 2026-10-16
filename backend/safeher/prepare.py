"""Download street data for a trip outside the configured areas ("go anywhere").

A trip's corridor (both points plus a margin) is fetched from OpenStreetMap in the background the
first time it's needed, checked, and kept in data/areas/trip-<hash>/ for later trips. Status lives
in that folder (status.json), and a lock file stops two server workers downloading the same area,
so every worker can answer "is it ready yet?".
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import networkx as nx

from . import config
from .areas import TRIP_PREFIX, Area, area_covering, area_to_json, get_area, is_ready, register_trip_area, timezone_at
from .geo import LatLon, haversine_m

_A = config.app()["anywhere"]
MAX_TRIP_M = float(_A["max_trip_km"]) * 1000
MARGIN_M = float(_A["corridor_margin_m"])
STALE_S = float(_A["stale_download_s"])
RETRY_S = float(_A["retry_failed_after_s"])

_pool = ThreadPoolExecutor(max_workers=int(_A["download_workers"]), thread_name_prefix="area-download")
_lock = threading.Lock()


class TooFar(ValueError):
    pass


def corridor(o: LatLon, d: LatLon) -> tuple[float, float, float, float]:
    """(south, west, north, east) around both points, rounded outward to ~100 m so nearby trips share it."""
    dlat = MARGIN_M / 111_320
    dlon = MARGIN_M / (111_320 * max(0.2, math.cos(math.radians((o[0] + d[0]) / 2))))
    step = 0.001
    down = lambda x: math.floor(x / step) * step  # noqa: E731
    up = lambda x: math.ceil(x / step) * step  # noqa: E731
    return (round(down(min(o[0], d[0]) - dlat), 3), round(down(min(o[1], d[1]) - dlon), 3),
            round(up(max(o[0], d[0]) + dlat), 3), round(up(max(o[1], d[1]) + dlon), 3))


def trip_area(o: LatLon, d: LatLon, name: str | None = None) -> Area:
    south, west, north, east = corridor(o, d)
    key = hashlib.sha1(f"{south},{west},{north},{east}".encode()).hexdigest()[:10]
    centre = ((south + north) / 2, (west + east) / 2)
    return Area(f"{TRIP_PREFIX}{key}", "Trip area", name or "Trip area", south, west, north, east,
                timezone_at(*centre))


def _status_path(a: Area):
    return a.dir / "status.json"


def _write_status(a: Area, state: str, message: str, **extra) -> None:
    a.dir.mkdir(parents=True, exist_ok=True)
    tmp = _status_path(a).with_suffix(".tmp")
    tmp.write_text(json.dumps({"area": a.id, "state": state, "message": message, "updated": time.time(), **extra}))
    tmp.replace(_status_path(a))


def status(area_id: str) -> dict:
    try:
        a = get_area(area_id)
    except KeyError:
        return {"area": area_id, "state": "unknown", "message": "No such trip area."}
    if is_ready(a):
        return {"area": a.id, "state": "ready", "message": "Street data ready."}
    try:
        s = json.loads(_status_path(a).read_text())
    except (FileNotFoundError, ValueError):
        return {"area": a.id, "state": "queued", "message": "Waiting to download street data."}
    if s["state"] in ("downloading", "queued") and time.time() - s["updated"] > STALE_S:
        s = {**s, "state": "failed", "message": "The download stopped responding. Try again."}
    return s


def _check(a: Area) -> str | None:
    """Reject downloads that can't support routing. Returns a problem, or None if fine."""
    from .routing import load_area

    load_area.cache_clear()
    data = load_area(a.id)
    g = data.graph
    if g.number_of_nodes() < int(_A["min_nodes"]):
        return f"only {g.number_of_nodes()} street points were found here"
    largest = max(nx.strongly_connected_components(g), key=len)
    if len(largest) / g.number_of_nodes() < float(_A["min_connected_share"]):
        return "the street network here is too fragmented to route on"
    return None


def _download(a: Area) -> None:
    from .osm import save_area

    a.dir.mkdir(parents=True, exist_ok=True)
    with open(a.dir / ".lock", "w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # another worker is downloading it
        if is_ready(a):
            return
        started = time.time()
        _write_status(a, "downloading", "Downloading street data from OpenStreetMap (first time only)…",
                      started=started)
        try:
            meta = save_area(a, write_meta=False)
            _write_status(a, "checking", "Checking the street data…", started=started)
            problem = _check(a)
            if problem:
                _write_status(a, "failed", f"Can't plan walks here: {problem}.", started=started)
                return
            (a.dir / "meta.json").write_text(json.dumps(meta, indent=2))  # marks the area ready
            _write_status(a, "ready", "Street data ready.", started=started, seconds=round(time.time() - started))
        except Exception as e:  # network, Overpass, parsing: report, don't crash the worker
            _write_status(a, "failed", f"Couldn't download street data: {type(e).__name__}: {str(e)[:160]}",
                          started=started)


def ensure(o: LatLon, d: LatLon, name: str | None = None) -> tuple[Area | None, dict | None]:
    """Return (ready area, None) or (None, status of the download now in progress)."""
    if haversine_m(o, d) > MAX_TRIP_M:
        raise TooFar(f"That's {haversine_m(o, d) / 1000:.1f} km in a straight line; SafeHer plans walks up to "
                     f"{MAX_TRIP_M / 1000:.0f} km.")
    a = area_covering(o, d)
    if a is not None:
        return a, None
    a = trip_area(o, d, name)
    with _lock:
        register_trip_area(a)
        a.dir.mkdir(parents=True, exist_ok=True)
        if not (a.dir / "area.json").exists():
            (a.dir / "area.json").write_text(json.dumps(area_to_json(a), indent=2))
        if is_ready(a):
            return a, None
        st = status(a.id)
        retry_failed = st["state"] == "failed" and time.time() - st.get("updated", 0) > RETRY_S
        if st["state"] in ("unknown", "queued") or retry_failed or (
                st["state"] in ("downloading", "checking") and time.time() - st["updated"] > STALE_S):
            _write_status(a, "queued", "Waiting to download street data.")
            _pool.submit(_download, a)
            st = status(a.id)
    return None, st
