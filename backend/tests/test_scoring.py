from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from safeher import signals as sig
from safeher.geo import haversine_m, polyline_length_m, split_polyline
from safeher.scoring import (
    DAY_WEIGHTS,
    NIGHT_WEIGHTS,
    band,
    night_fraction,
    score_route,
    score_segment,
    weights_at,
)
from safeher.signals import SIGNALS, Reading

IST = ZoneInfo("Asia/Kolkata")
BENGALURU = (12.9716, 77.5946)
NOW = datetime(2026, 10, 4, 21, 0, tzinfo=IST)


def readings(**overrides: Reading) -> dict[str, Reading]:
    base = {k: Reading(80.0, 0.8) for k in SIGNALS}
    base.update(overrides)
    return base


# --- weights ------------------------------------------------------------------------------


def test_weights_sum_to_one():
    assert sum(NIGHT_WEIGHTS.values()) == pytest.approx(1.0)
    assert sum(DAY_WEIGHTS.values()) == pytest.approx(1.0)
    assert sum(weights_at(0.37).values()) == pytest.approx(1.0)


def test_lighting_matters_more_at_night():
    assert weights_at(1.0)[sig.LIGHTING] > weights_at(0.0)[sig.LIGHTING]


@pytest.mark.parametrize(
    "hour, expected",
    [(13, 0.0), (22, 1.0), (2, 1.0)],
)
def test_night_fraction_day_and_night(hour, expected):
    when = datetime(2026, 10, 4, hour, 0, tzinfo=IST)
    assert night_fraction(when, *BENGALURU) == pytest.approx(expected)


def test_night_fraction_blends_around_sunset():
    # Bengaluru sunset on 4 Oct 2026 is ~18:08 IST; at sunset the blend should be about halfway.
    from astral import LocationInfo
    from astral.sun import sun

    s = sun(LocationInfo(latitude=BENGALURU[0], longitude=BENGALURU[1]).observer, date=NOW.date(), tzinfo=IST)
    assert night_fraction(s["sunset"], *BENGALURU) == pytest.approx(0.5, abs=0.01)
    assert night_fraction(s["sunset"] - timedelta(minutes=20), *BENGALURU) == 0.0
    assert night_fraction(s["sunset"] + timedelta(minutes=20), *BENGALURU) == 1.0


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        night_fraction(datetime(2026, 10, 4, 21, 0), *BENGALURU)


# --- signals ------------------------------------------------------------------------------


def test_lighting_tags():
    assert sig.lighting({"lit": "yes"}, 0, night=True).value == 100
    assert sig.lighting({"lit": "no"}, 0, night=True).value == 0
    assert sig.lighting({"lit": "no"}, 0, night=True).confidence > 0.5
    lamps = sig.lighting({}, 2, night=True)
    assert lamps.value == 100 and lamps.confidence == 0.7
    prior = sig.lighting({"highway": "path"}, 0, night=True)
    assert prior.confidence < 0.5  # a guess must never look as certain as a tag
    assert sig.lighting({}, 0, night=True).confidence == 0


@pytest.mark.parametrize("dist, value", [(0, 100), (150, 100), (475, 50), (800, 0), (2000, 0)])
def test_transit_distance(dist, value):
    assert sig.transit(dist).value == pytest.approx(value)


def test_safe_spaces_saturate_and_weight_police():
    assert sig.safe_spaces([]).value == 0
    assert sig.safe_spaces(["police", "hospital"]).value == 100  # 1.5 + 1.5 = 3
    assert sig.safe_spaces(["pharmacy"] * 10).value == 100
    assert sig.safe_spaces(["police"]).value > sig.safe_spaces(["pharmacy"]).value


def test_reports_decay_with_half_life():
    fresh = sig.reports([sig.Report("harassment", NOW, "corroborated")], NOW)
    week_old = sig.reports([sig.Report("harassment", NOW - timedelta(days=7), "corroborated")], NOW)
    assert fresh.value == pytest.approx(60)
    assert week_old.value == pytest.approx(80)  # half the penalty after 7 days


def test_reports_expire_and_ignore_future():
    old = sig.reports([sig.Report("harassment", NOW - timedelta(days=31), "corroborated")], NOW)
    future = sig.reports([sig.Report("harassment", NOW + timedelta(days=1), "corroborated")], NOW)
    assert old.value == 100 and future.value == 100


def test_unverified_reports_count_half():
    pending = sig.reports([sig.Report("harassment", NOW, "pending")], NOW)
    assert pending.value == pytest.approx(80)


def test_positive_reports_cannot_exceed_100():
    many = [sig.Report("positive", NOW, "corroborated")] * 5
    assert sig.reports(many, NOW).value == 100


def test_reports_score_never_negative():
    many = [sig.Report("harassment", NOW, "corroborated")] * 10
    assert sig.reports(many, NOW).value == 0


def test_reading_validates_range():
    with pytest.raises(ValueError):
        Reading(120, 0.5)
    with pytest.raises(ValueError):
        Reading(50, 1.5)


# --- segments -----------------------------------------------------------------------------


def test_segment_score_is_weighted_average():
    w = weights_at(1.0)
    s = score_segment(readings(), w)
    assert s.score == pytest.approx(80)
    assert sum(s.effective_weights.values()) == pytest.approx(1.0)


def test_missing_signal_redistributes_weight_instead_of_scoring_zero():
    w = weights_at(1.0)
    s = score_segment(readings(lighting=sig.NO_DATA), w)
    assert s.score == pytest.approx(80)  # not dragged down by "no data"
    assert s.effective_weights[sig.LIGHTING] == 0
    assert s.confidence < 1


def test_segment_with_too_little_data_is_unscored():
    w = weights_at(1.0)
    s = score_segment({k: sig.NO_DATA for k in SIGNALS}, w)
    assert s.score is None


def test_missing_signal_key_raises():
    with pytest.raises(ValueError):
        score_segment({sig.LIGHTING: Reading(50, 1)}, weights_at(1.0))


# --- routes -------------------------------------------------------------------------------


def _route(scores: list[float], length: float = 50.0):
    w = weights_at(1.0)
    return [(length, score_segment({k: Reading(v, 1.0) for k in SIGNALS}, w)) for v in scores], w


def test_dark_stretch_is_not_averaged_away():
    """Spec §7.1: a great route with one 300 m dark lane must score below an evenly decent one."""
    great_but_dark_lane, w = _route([95] * 34 + [10] * 6)  # 2 km, 300 m bad
    mean_a = sum([95] * 34 + [10] * 6) / 40
    even, _ = _route([mean_a] * 40)  # same average everywhere
    a = score_route(great_but_dark_lane, w)
    b = score_route(even, w)
    assert a.mean == pytest.approx(b.mean)
    assert a.score < b.score


def test_route_score_formula():
    segs, w = _route([100] * 9 + [0])
    r = score_route(segs, w)
    # mean = 90; the bottom 10% of length scores 0 → 0.8*90 + 0.2*0
    assert r.score == pytest.approx(72.0)
    assert r.band == "Mixed"


def test_longer_segments_weigh_more():
    w = weights_at(1.0)
    long_good = (450.0, score_segment({k: Reading(100, 1.0) for k in SIGNALS}, w))
    short_bad = (50.0, score_segment({k: Reading(0, 1.0) for k in SIGNALS}, w))
    r = score_route([long_good, short_bad], w)
    assert r.mean == pytest.approx(90)


def test_route_with_little_coverage_is_not_scored():
    w = weights_at(1.0)
    empty = score_segment({k: sig.NO_DATA for k in SIGNALS}, w)
    good = score_segment(readings(), w)
    r = score_route([(50, good), (50, empty), (50, empty)], w)
    assert r.score is None and r.coverage == pytest.approx(1 / 3)


def test_bands_avoid_safe_unsafe_wording():
    assert band(80) == "More support"
    assert band(60) == "Mixed"
    assert band(10) == "Less support"
    for label in ("More support", "Mixed", "Less support"):
        assert "safe" not in label.lower()


# --- geometry -----------------------------------------------------------------------------


def test_haversine_known_distance():
    # ~1 degree of latitude ≈ 111.2 km
    assert haversine_m((12.0, 77.0), (13.0, 77.0)) == pytest.approx(111_195, rel=1e-3)


def test_split_polyline_preserves_length():
    line = [(12.9716, 77.5946), (12.9750, 77.6000), (12.9762, 77.6101)]
    pieces = split_polyline(line, 50)
    assert sum(polyline_length_m(p) for p in pieces) == pytest.approx(polyline_length_m(line), rel=1e-6)
    assert all(polyline_length_m(p) <= 50 * 1.5 for p in pieces)
    assert pieces[0][0] == line[0] and pieces[-1][-1] == line[-1]


# --- monotonicity: found by random end-to-end testing -------------------------------------


def test_reports_never_move_a_score_the_wrong_way():
    """A negative report can only lower a stretch's score; a positive one can only raise it."""
    import random

    rng = random.Random(7)
    for _ in range(5000):
        w = weights_at(rng.random())
        base = {k: Reading(rng.uniform(0, 100), rng.choice([0.0, 0.3, 0.45, 0.7, 0.9])) for k in SIGNALS}
        existing = [sig.Report(rng.choice(list(sig.REPORT_SEVERITY)), NOW - timedelta(days=rng.uniform(0, 29)),
                               rng.choice(["pending", "corroborated"])) for _ in range(rng.randint(0, 3))]
        before = score_segment({**base, sig.REPORTS: sig.reports(existing, NOW)}, w).score
        neg = score_segment({**base, sig.REPORTS: sig.reports(existing + [sig.Report("harassment", NOW)], NOW)}, w).score
        pos = score_segment({**base, sig.REPORTS: sig.reports(existing + [sig.Report("positive", NOW)], NOW)}, w).score
        if before is not None:
            assert neg <= before + 1e-9 and pos >= before - 1e-9
