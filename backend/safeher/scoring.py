"""Rule-based, transparent route scoring (design spec §7).

segment_score = Σ effective_weight_i × signal_i      (weights renormalised by confidence)
route_score   = 0.8 × length_weighted_mean(segment_score) + 0.2 × length_weighted_p10(segment_score)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from astral import LocationInfo
from astral.sun import sun

from .signals import INCIDENTS, LIGHTING, REPORTS, SAFE_SPACES, SIGNALS, TRANSIT, Reading

WEIGHTS_VERSION = "2026-10-04.1"

NIGHT_WEIGHTS = {LIGHTING: 0.30, TRANSIT: 0.15, SAFE_SPACES: 0.25, INCIDENTS: 0.15, REPORTS: 0.15}
DAY_WEIGHTS = {LIGHTING: 0.05, TRANSIT: 0.20, SAFE_SPACES: 0.30, INCIDENTS: 0.20, REPORTS: 0.25}

TRANSITION = timedelta(minutes=30)  # weights blend over 30 min centred on sunrise / sunset
MEAN_SHARE, WORST_SHARE = 0.8, 0.2
WORST_PERCENTILE = 0.10
MIN_CONFIDENCE_MASS = 0.15  # below this, a segment is "insufficient data" rather than scored

BANDS = ((75, "More support"), (50, "Mixed"), (0, "Less support"))


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
    if not scored or coverage < 0.5:
        return RouteScore(None, None, None, None, signal_means, signal_conf, coverage, weights,
                          segments=[s for _, s in segments])

    mean = sum(score * length for score, length in scored) / scored_len
    worst = _length_weighted_percentile(scored, WORST_PERCENTILE)
    total = MEAN_SHARE * mean + WORST_SHARE * worst
    return RouteScore(round(total, 1), band(total), mean, worst, signal_means, signal_conf, coverage, weights,
                      segments=[s for _, s in segments])
