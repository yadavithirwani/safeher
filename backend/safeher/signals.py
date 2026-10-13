"""The five per-segment signals (design spec §7.2). Each returns a Reading: 0-100 value + 0-1 confidence.

A reading with confidence 0 means "no data": its weight is redistributed to the other signals
rather than being treated as a low score.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

LIGHTING = "lighting"
TRANSIT = "transit"
SAFE_SPACES = "safe_spaces"
INCIDENTS = "incidents"
REPORTS = "reports"
SIGNALS = (LIGHTING, TRANSIT, SAFE_SPACES, INCIDENTS, REPORTS)


@dataclass(frozen=True)
class Reading:
    value: float  # 0-100, higher = more support
    confidence: float  # 0-1, how much data backs the value
    note: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.value <= 100:
            raise ValueError(f"value out of range: {self.value}")
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"confidence out of range: {self.confidence}")


NO_DATA = Reading(0.0, 0.0, "no data")


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


# --- Lighting -------------------------------------------------------------------------------

# When a street has no lit=* tag and no mapped lamps, fall back to a weak prior from road class:
# arterial roads in Indian cities are usually lit; footpaths and service lanes often aren't.
_HIGHWAY_LIGHT_PRIOR = {
    "trunk": 80, "primary": 80, "secondary": 75, "tertiary": 65,
    "residential": 50, "living_street": 50, "unclassified": 45,
    "service": 35, "footway": 40, "pedestrian": 60, "path": 25, "track": 15, "steps": 35,
}


def lighting(tags: dict | None, lamps_within_25m: int, night: bool,
             viirs_percentile: float | None = None) -> Reading:
    tags = tags or {}
    lit_tag = tags.get("lit", "")
    if isinstance(lit_tag, list):  # OSMnx merges ways; a mix of lit and unlit parts reads as "limited"
        values = {str(v).lower() for v in lit_tag}
        lit_tag = "limited" if len(values) > 1 else values.pop()
    lit = str(lit_tag).lower()
    if lit in {"yes", "24/7", "automatic", "sunset-sunrise", "limited", "interval"}:
        value = 70.0 if lit in {"limited", "interval"} else 100.0
        return Reading(value, 0.9, f"tagged lit={lit}")
    if lit == "no":
        return Reading(0.0 if night else 50.0, 0.9, "tagged unlit")
    if lamps_within_25m > 0:
        return Reading(min(100.0, 60.0 + 20.0 * lamps_within_25m), 0.7, f"{lamps_within_25m} street lamps mapped")
    if viirs_percentile is not None:
        # Satellite night lights, ranked within the area: coarse (~500 m) but measured, not guessed.
        return Reading(round(100.0 * viirs_percentile, 1), 0.45, "satellite night-time lights (VIIRS)")
    highway = tags.get("highway")
    if isinstance(highway, list):
        highway = highway[0]
    if highway in _HIGHWAY_LIGHT_PRIOR:
        return Reading(float(_HIGHWAY_LIGHT_PRIOR[highway]), 0.3, f"estimated from road type ({highway})")
    return NO_DATA


# --- Transit --------------------------------------------------------------------------------


def transit(nearest_stop_m: float | None) -> Reading:
    """≤150 m to an active stop = 100, ≥800 m = 0, linear between."""
    if nearest_stop_m is None:
        return Reading(0.0, 0.6, "no stop within search radius")
    if nearest_stop_m <= 150:
        value = 100.0
    elif nearest_stop_m >= 800:
        value = 0.0
    else:
        value = 100.0 * (800 - nearest_stop_m) / (800 - 150)
    return Reading(value, 0.8, f"nearest stop {nearest_stop_m:.0f} m")


# --- Safe spaces ----------------------------------------------------------------------------

STRONG_SAFE_SPACES = {"police", "hospital"}  # weighted x1.5 per spec


def safe_spaces(open_place_types: list[str]) -> Reading:
    """Places open now within 150 m. Saturates at a weighted count of 3."""
    weighted = sum(1.5 if t in STRONG_SAFE_SPACES else 1.0 for t in open_place_types)
    value = min(1.0, weighted / 3.0) * 100.0
    return Reading(value, 0.7, f"{len(open_place_types)} open places within 150 m")


# --- Historical incidents -------------------------------------------------------------------


def incidents(city_percentile: float | None, confidence: float, note: str = "district-level official data") -> Reading:
    """Inverse of incident-density percentile (0 = lowest in city). Coarse by design."""
    if city_percentile is None:
        return NO_DATA
    return Reading(_clamp(100.0 * (1.0 - city_percentile)), confidence, note)


# --- Recent community reports ---------------------------------------------------------------

REPORT_SEVERITY = {
    "harassment": 40.0,
    "unsafe_crowd": 25.0,
    "isolated_stretch": 20.0,
    "broken_light": 15.0,
    "poor_footpath": 10.0,
    "unsafe_washroom": 10.0,
    "positive": -15.0,  # negative severity = adds support
}
VERIFICATION_FACTOR = {"pending": 0.5, "corroborated": 1.0, "partner": 1.0}
REPORT_HALF_LIFE_DAYS = 7.0
REPORT_MAX_AGE_DAYS = 30.0


@dataclass(frozen=True)
class Report:
    category: str
    created_at: datetime
    verification: str = "pending"


def reports(nearby: list[Report], now: datetime) -> Reading:
    """Starts at 100; each report within 50 m subtracts severity x verification x age decay."""
    penalty = 0.0
    counted = 0
    for r in nearby:
        age_days = (now - r.created_at).total_seconds() / 86_400
        if age_days < 0 or age_days > REPORT_MAX_AGE_DAYS:
            continue
        decay = 0.5 ** (age_days / REPORT_HALF_LIFE_DAYS)
        penalty += REPORT_SEVERITY[r.category] * VERIFICATION_FACTOR[r.verification] * decay
        counted += 1
    note = f"{counted} recent reports" if counted else "no recent reports"
    # Absence of reports is weak evidence, so confidence grows with the number of reports seen.
    confidence = 0.4 if counted == 0 else min(0.9, 0.5 + 0.1 * counted)
    return Reading(_clamp(100.0 - penalty), confidence, note)
