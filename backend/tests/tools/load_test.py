"""Load test: many simulated users hitting a running server at once.

    .venv/bin/python -m tests.tools.load_test --base http://localhost:8765 --users 40 --seconds 60

Each user loops through a realistic mix: plan (random street-node trips, random times), explain,
search, nearby, map layers, and a live-sharing session (create → update → read → stop). Community
reports are not sent, so the server's real report file is left untouched.
Prints per-endpoint counts, errors and latency percentiles; exits non-zero on any 5xx.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time
from collections import defaultdict

import httpx

from safeher.areas import AREAS
from safeher.geo import haversine_m
from safeher.routing import load_area

TIMES = ["07:30", "13:00", "18:10", "21:30", "23:45"]
QUERIES = ["metro", "road", "hospital", "starbucks", "12th main", "aurobindo", "saket", "cafe"]


def trip_pool(n: int, seed: int) -> list[tuple[str, tuple, tuple]]:
    rng = random.Random(seed)
    pool = []
    for area_id in sorted(AREAS):
        data = load_area(area_id)
        nodes = list(data.graph.nodes)
        while sum(p[0] == area_id for p in pool) < n:
            o, d = (data.node_latlon(rng.choice(nodes)) for _ in range(2))
            if 600 <= haversine_m(o, d) <= 3500:
                pool.append((area_id, o, d))
    return pool


class Stats:
    def __init__(self):
        self.lat: dict[str, list[float]] = defaultdict(list)
        self.codes: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.failures: list[str] = []

    async def hit(self, client: httpx.AsyncClient, name: str, method: str, url: str, **kw) -> httpx.Response | None:
        t0 = time.perf_counter()
        try:
            r = await client.request(method, url, **kw)
        except httpx.HTTPError as e:
            self.codes[name][0] += 1
            self.failures.append(f"{name}: {type(e).__name__}")
            return None
        self.lat[name].append(time.perf_counter() - t0)
        self.codes[name][r.status_code] += 1
        if r.status_code >= 500:
            self.failures.append(f"{name} {r.status_code}: {r.text[:160]}")
        return r


async def user(uid: int, base: str, stop_at: float, pool, stats: Stats):
    rng = random.Random(uid)
    async with httpx.AsyncClient(base_url=base, timeout=60) as c:
        while time.time() < stop_at:
            area_id, o, d = rng.choice(pool)
            body = {"origin": {"lat": o[0], "lon": o[1]}, "destination": {"lat": d[0], "lon": d[1]},
                    "depart_at": f"2026-10-14T{rng.choice(TIMES)}:00"}
            await stats.hit(c, "plan", "POST", "/api/plan", json=body)
            await stats.hit(c, "explain", "POST", "/api/explain", json={**body, "lang": rng.choice(["en", "hi"])})
            await stats.hit(c, "search", "GET", "/api/search", params={"q": rng.choice(QUERIES), "area": area_id})
            await stats.hit(c, "nearby", "GET", "/api/nearby", params={
                "lat": o[0], "lon": o[1], "kind": rng.choice(["police", "toilets", "hospital", "pharmacy", "open_places"]),
                "depart_at": body["depart_at"]})
            if rng.random() < 0.2:
                await stats.hit(c, "layers", "GET", f"/api/layers/{area_id}")
            if rng.random() < 0.3:
                r = await stats.hit(c, "share_create", "POST", "/api/trips", json={"route": [[o[1], o[0]], [d[1], d[0]]]})
                if r is not None and r.status_code == 201:
                    t = r.json()
                    auth = {"Authorization": f"Bearer {t['owner_token']}"}
                    await stats.hit(c, "share_update", "PUT", f"/api/trips/{t['viewer_token']}/location",
                                    json={"lat": o[0], "lon": o[1], "accuracy_m": 8}, headers=auth)
                    await stats.hit(c, "share_view", "GET", f"/api/trips/{t['viewer_token']}")
                    await stats.hit(c, "share_stop", "DELETE", f"/api/trips/{t['viewer_token']}", headers=auth)


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


async def main_async(args) -> int:
    pool = trip_pool(args.trips, args.seed)
    stats = Stats()
    async with httpx.AsyncClient(base_url=args.base, timeout=30) as c:  # warm up caches per area
        for area_id, o, d in pool[:: max(1, len(pool) // 4)]:
            await c.post("/api/plan", json={"origin": {"lat": o[0], "lon": o[1]},
                                            "destination": {"lat": d[0], "lon": d[1]}, "depart_at": "2026-10-14T21:30:00"})
    start = time.time()
    await asyncio.gather(*(user(i, args.base, start + args.seconds, pool, stats) for i in range(args.users)))
    elapsed = time.time() - start
    total = sum(len(v) for v in stats.lat.values())
    print(f"\n{args.users} users × {elapsed:.0f}s · {total} requests · {total / elapsed:.1f} req/s · seed {args.seed}")
    print(f"{'endpoint':<14}{'count':>7}{'errors':>8}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}{'max ms':>9}  status codes")
    for name in sorted(stats.lat, key=lambda n: -len(stats.lat[n])):
        xs = stats.lat[name]
        errors = sum(n for code, n in stats.codes[name].items() if code == 0 or code >= 500)
        print(f"{name:<14}{len(xs):>7}{errors:>8}{pct(xs, .5) * 1e3:>9.0f}{pct(xs, .95) * 1e3:>9.0f}"
              f"{pct(xs, .99) * 1e3:>9.0f}{max(xs) * 1e3:>9.0f}  {dict(stats.codes[name])}")
    if stats.failures:
        print(f"\n{len(stats.failures)} failures, first few:", *stats.failures[:5], sep="\n  ")
    return 1 if stats.failures else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8765")
    ap.add_argument("--users", type=int, default=40)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--trips", type=int, default=30, help="random trips per area")
    ap.add_argument("--seed", type=int, default=int(time.time()))
    sys.exit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
