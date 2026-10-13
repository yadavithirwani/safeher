"""The optimised Plan B must give exactly the same output as Anushka's original (v1)."""

import random
from datetime import datetime

import pytest

from safeher import backup
from safeher.areas import AREAS
from safeher.plan import TripContext, edge_support
from safeher.routing import candidate_paths, load_area, path_edges
from safeher.scoring import night_fraction, weights_at
from tests.reference import backup_v1

pytestmark = pytest.mark.skipif(
    not all((a.dir / "walk.graphml").exists() for a in AREAS.values()), reason="OSM snapshots not fetched")


def trips(seed=99, per_area=3):
    rng = random.Random(seed)
    out = []
    for area_id in sorted(AREAS):
        data = load_area(area_id)
        nodes = list(data.graph.nodes)
        while sum(t[0] == area_id for t in out) < per_area:
            o, d = (data.node_latlon(rng.choice(nodes)) for _ in range(2))
            from safeher.geo import haversine_m
            if 800 <= haversine_m(o, d) <= 3000:
                out.append((area_id, o, d, rng.choice(["06:40", "13:00", "21:30", "22:50", "23:45"])))
    return out


@pytest.mark.parametrize("trip", trips(), ids=lambda t: f"{t[0]}@{t[3]}")
def test_optimised_plan_b_matches_original(trip):
    area_id, o, d, t = trip
    data = load_area(area_id)
    when = datetime.fromisoformat(f"2026-10-14T{t}:00").replace(tzinfo=data.area.tz)
    night = night_fraction(when, *data.area.center)
    ctx = TripContext(data, when, night, weights_at(night))
    old, new = backup_v1.BackupCoverage(data, when), backup.BackupCoverage(data, when)
    for _, nodes in candidate_paths(data, o, d, edge_support(ctx)):
        edges = path_edges(data, nodes)
        a, b = old.describe(edges), new.describe(edges)
        a.pop("limitations"), b.pop("limitations")
        assert a == b
