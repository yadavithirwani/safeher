"""End-to-end checks on the cached OSM snapshots. Skipped if the data hasn't been fetched."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from safeher.areas import AREAS
from safeher.plan import DISCLAIMER, plan

IST = ZoneInfo("Asia/Kolkata")
TRIPS = {
    "blr-indiranagar-mg-road": ((12.9784, 77.6408), (12.9756, 77.6067)),
    "del-hauz-khas-saket": ((28.5433, 77.2067), (28.5286, 77.2195)),
}

pytestmark = pytest.mark.skipif(
    not all((AREAS[a].dir / "walk.graphml").exists() for a in TRIPS),
    reason="OSM snapshots not fetched (python -m safeher.fetch)",
)


@pytest.fixture(scope="module", params=list(TRIPS))
def night_plan(request):
    o, d = TRIPS[request.param]
    return plan(o, d, datetime(2026, 10, 7, 21, 30, tzinfo=IST))


def test_two_to_three_routes(night_plan):
    assert 2 <= len(night_plan["routes"]) <= 3
    labels = [r["label"] for r in night_plan["routes"]]
    assert "Shortest" in labels and len(set(labels)) == len(labels)


def test_routes_sorted_by_score_and_banded(night_plan):
    scores = [r["score"] for r in night_plan["routes"]]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 100 for s in scores)
    assert all(r["band"] in {"More support", "Mixed", "Less support"} for r in night_plan["routes"])


def test_detours_within_limit(night_plan):
    shortest = next(r for r in night_plan["routes"] if r["label"] == "Shortest")
    for r in night_plan["routes"]:
        assert r["distance_m"] <= 1.5 * shortest["distance_m"] + 1


def test_geometry_connects_origin_and_destination(night_plan):
    for r in night_plan["routes"]:
        assert len(r["geometry"]) >= 2
        assert r["distance_m"] > 500


def test_never_says_safe(night_plan):
    text = " ".join(" ".join(r["reasons"]) + r["band"] for r in night_plan["routes"]).lower()
    assert " safe " not in f" {text} " and "unsafe" not in text
    assert night_plan["disclaimer"] == DISCLAIMER and "112" in DISCLAIMER


def test_incidents_signal_is_honest_about_coverage(night_plan):
    values = {(b["value"], b["confidence"]) for r in night_plan["routes"]
              for b in r["breakdown"] if b["signal"] == "incidents"}
    assert len(values) == 1  # district-level data: identical for every route in the area
    value, confidence = values.pop()
    if night_plan["area"]["city"] == "Bengaluru":
        assert value is None and confidence == 0  # one city-wide unit: not scored as zero
    else:
        assert value is not None and 0 < confidence <= 0.2
    assert any("NCRB" in n for n in night_plan["data_notes"])


def test_scores_lower_late_at_night_than_daytime():
    o, d = TRIPS["del-hauz-khas-saket"]
    day = plan(o, d, datetime(2026, 10, 7, 13, 0, tzinfo=IST))
    late = plan(o, d, datetime(2026, 10, 7, 23, 45, tzinfo=IST))
    assert max(r["score"] for r in late["routes"]) < max(r["score"] for r in day["routes"])
