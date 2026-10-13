"""Rule-based, transparent route scoring (design spec §7). Parameters: config/scoring.json.

segment_score = Σ effective_weight_i × signal_i      (weights renormalised by confidence)
route_score   = mean_share × length_weighted_mean(segment_score)
              + worst_share × length_weighted_percentile(segment_score, worst_percentile)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from astral import LocationInfo
from astral.sun import sun

from . import config
from .signals import SIGNALS, Reading

_C = config.scoring()
WEIGHTS_VERSION = _C["version"]

NIGHT_WEIGHTS = {k: float(_C["weights"]["night"][k]) for k in SIGNALS}
DAY_WEIGHTS = {k: float(_C["weights"]["day"][k]) for k in SIGNALS}
for _name, _w in (("night", NIGHT_WEIGHTS), ("day", DAY_WEIGHTS)):
    if abs(sum(_w.values()) - 1) > 1e-6:
        raise RuntimeError(f"config/scoring.json: {_name} weights must sum to 1, got {sum(_w.values())}")

TRANSITION = timedelta(minutes=_C["weights"]["transition_minutes"])  # blend centred on sunrise / sunset
MEAN_SHARE, WORST_SHARE = float(_C["route"]["mean_share"]), float(_C["route"]["worst_share"])
WORST_PERCENTILE = float(_C["route"]["worst_percentile"])
MIN_CONFIDENCE_MASS = float(_C["route"]["min_confidence_mass"])  # below this a segment is unscored
MIN_COVERAGE = float(_C["route"]["min_coverage"])  # below this share of length a route is unscored

BANDS = tuple(sorted(((b["min"], b["label"]) for b in _C["bands"]), reverse=True))


def band(score: float) -> str:
    return next(label for floor, label in BANDS if score >= floor)


# --- Time-aware weights ---------------------------------------------------------------------


def night_fraction(when: datetime, lat: float, lon: float) -> float:
    """1.0 at night, 0.0 in daytime, linear over a 30-min window around sunrise and sunset.

    `when` must be timezone-aware.
    """
    if when.tzinfo is None:
        raise ValueError("departure time must be timezone-aware")
    loc = LocationInfo(latitude=lat, longitude=lon, timezone="UTC")
    s = sun(loc.observer, date=when.date(), tzinfo=when.tzinfo)
    half = TRANSITION / 2

    def ramp(t: datetime, edge: datetime) -> float:  # 0 before edge-half, 1 after edge+half
        return max(0.0, min(1.0, (t - (edge - half)) / TRANSITION))

    after_sunrise = ramp(when, s["sunrise"])  # 0 → 1 as day begins
    after_sunset = ramp(when, s["sunset"])  # 0 → 1 as night begins
    return 1.0 - after_sunrise + after_sunset


def weights_at(fraction_night: float) -> dict[str, float]:
    f = max(0.0, min(1.0, fraction_night))
    return {k: f * NIGHT_WEIGHTS[k] + (1 - f) * DAY_WEIGHTS[k] for k in SIGNALS}


# --- Segment and route scores ---------------------------------------------------------------


@dataclass(frozen=True)
class SegmentScore:
    score: float | None  # None = not enough data to score
    readings: dict[str, Reading]
    effective_weights: dict[str, float]
    confidence: float  # share of base weight backed by data, 0-1


def score_segment(readings: dict[str, Reading], weights: dict[str, float]) -> SegmentScore:
    missing = set(SIGNALS) - readings.keys()
    if missing:
        raise ValueError(f"missing signals: {sorted(missing)}")
    mass = {k: weights[k] * readings[k].confidence for k in SIGNALS}
    total = sum(mass.values())
    confidence = total / sum(weights.values())
    if confidence < MIN_CONFIDENCE_MASS:
        return SegmentScore(None, readings, {k: 0.0 for k in SIGNALS}, confidence)
    effective = {k: m / total for k, m in mass.items()}
    score = sum(effective[k] * readings[k].value for k in SIGNALS)
    return SegmentScore(score, readings, effective, confidence)


def _length_weighted_percentile(pairs: list[tuple[float, float]], q: float) -> float:
    """pairs = (score, length). Returns the score at which q of the route length lies below."""
    ordered = sorted(pairs)
    total = sum(length for _, length in ordered)
    cutoff = q * total
    acc = 0.0
    for score, length in ordered:
        acc += length
        if acc >= cutoff:
            return score
    return ordered[-1][0]


@dataclass
class RouteScore:
    score: float | None
    band: str | None
    mean: float | None
    worst_stretch: float | None
    signal_means: dict[str, float | None]  # length-weighted mean per signal, for the breakdown bars
    signal_confidence: dict[str, float]
    coverage: float  # share of route length that could be scored
    weights: dict[str, float]
    weights_version: str = WEIGHTS_VERSION
    segments: list[SegmentScore] = field(default_factory=list, repr=False)


def score_route(segments: list[tuple[float, SegmentScore]], weights: dict[str, float]) -> RouteScore:
    """segments = (length_m, SegmentScore) in route order."""
    if not segments:
        raise ValueError("route has no segments")
    total_len = sum(length for length, _ in segments)
    scored = [(s.score, length) for length, s in segments if s.score is not None]
    scored_len = sum(length for _, length in scored)

    signal_means: dict[str, float | None] = {}
    signal_conf: dict[str, float] = {}
    for k in SIGNALS:
        with_data = [(length, s.readings[k]) for length, s in segments if s.readings[k].confidence > 0]
        data_len = sum(length for length, _ in with_data)
        signal_means[k] = (sum(length * r.value for length, r in with_data) / data_len) if data_len else None
        signal_conf[k] = sum(length * s.readings[k].confidence for length, s in segments) / total_len

    coverage = scored_len / total_len
    if not scored or coverage < MIN_COVERAGE:
        return RouteScore(None, None, None, None, signal_means, signal_conf, coverage, weights,
                          segments=[s for _, s in segments])

    mean = sum(score * length for score, length in scored) / scored_len
    worst = _length_weighted_percentile(scored, WORST_PERCENTILE)
    total = MEAN_SHARE * mean + WORST_SHARE * worst
    return RouteScore(round(total, 1), band(total), mean, worst, signal_means, signal_conf, coverage, weights,
                      segments=[s for _, s in segments])
