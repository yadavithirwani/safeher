"""The five per-segment signals (design spec §7.2). Each returns a Reading: 0-100 value + 0-1 confidence.

A reading with confidence 0 means "no data": its weight is redistributed to the other signals
rather than being treated as a low score. All thresholds and values come from config/scoring.json.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import config

LIGHTING = "lighting"
TRANSIT = "transit"
SAFE_SPACES = "safe_spaces"
INCIDENTS = "incidents"
REPORTS = "reports"
SIGNALS = (LIGHTING, TRANSIT, SAFE_SPACES, INCIDENTS, REPORTS)

_C = config.scoring()
_LIGHT, _TRANSIT, _SAFE, _REPORTS = _C["lighting"], _C["transit"], _C["safe_spaces"], _C["reports"]


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

LAMP_RADIUS_M = float(_LIGHT["lamp_radius_m"])


def lighting(tags: dict | None, lamps_nearby: int, night: bool,
             viirs_percentile: float | None = None) -> Reading:
    """Best available source wins: OSM lit tag → mapped lamps → VIIRS → road-type estimate."""
    tags = tags or {}
    lit_tag = tags.get("lit", "")
    if isinstance(lit_tag, list):  # OSMnx merges ways; a mix of lit and unlit parts reads as "limited"
        values = {str(v).lower() for v in lit_tag}
        lit_tag = "limited" if len(values) > 1 else values.pop()
    lit = str(lit_tag).lower()
    if lit in _LIGHT["lit_values"]:
        return Reading(float(_LIGHT["lit_values"][lit]), _LIGHT["tag_confidence"], f"tagged lit={lit}")
    if lit == "no":
        value = _LIGHT["unlit_value_night"] if night else _LIGHT["unlit_value_day"]
        return Reading(float(value), _LIGHT["tag_confidence"], "tagged unlit")
    if lamps_nearby > 0:
        value = min(100.0, _LIGHT["lamp_base"] + _LIGHT["lamp_step"] * lamps_nearby)
        return Reading(float(value), _LIGHT["lamp_confidence"], f"{lamps_nearby} street lamps mapped")
    if viirs_percentile is not None:
        # Satellite night lights, ranked within the area: coarse (~500 m) but measured, not guessed.
        return Reading(round(100.0 * viirs_percentile, 1), _LIGHT["viirs_confidence"],
                       "satellite night-time lights (VIIRS)")
    highway = tags.get("highway")
    if isinstance(highway, list):
        highway = highway[0]
    prior = _LIGHT["road_type_prior"]
    if highway in prior:
        return Reading(float(prior[highway]), _LIGHT["road_type_confidence"], f"estimated from road type ({highway})")
    return NO_DATA


# --- Transit --------------------------------------------------------------------------------

TRANSIT_SEARCH_M = float(_TRANSIT["search_radius_m"])


def transit(nearest_stop_m: float | None) -> Reading:
    """Full score within `full_score_within_m` of an active stop, zero beyond `zero_score_beyond_m`."""
    full, zero = _TRANSIT["full_score_within_m"], _TRANSIT["zero_score_beyond_m"]
    if nearest_stop_m is None:
        return Reading(0.0, _TRANSIT["confidence_when_none_found"], "no stop within search radius")
    if nearest_stop_m <= full:
        value = 100.0
    elif nearest_stop_m >= zero:
        value = 0.0
    else:
        value = 100.0 * (zero - nearest_stop_m) / (zero - full)
    return Reading(value, _TRANSIT["confidence"], f"nearest stop {nearest_stop_m:.0f} m")


# --- Safe spaces ----------------------------------------------------------------------------

SAFE_SPACE_RADIUS_M = float(_SAFE["radius_m"])
SAFE_SPACE_KINDS = frozenset(_SAFE["kinds"])
STRONG_SAFE_SPACES = frozenset(_SAFE["strong_kinds"])


def safe_spaces(open_place_types: list[str]) -> Reading:
    """Open places nearby, saturating at a weighted count; police and hospitals count extra."""
    weighted = sum(_SAFE["strong_multiplier"] if t in STRONG_SAFE_SPACES else 1.0 for t in open_place_types)
    value = min(1.0, weighted / _SAFE["saturation"]) * 100.0
    return Reading(value, _SAFE["confidence"], f"{len(open_place_types)} open places within {SAFE_SPACE_RADIUS_M:.0f} m")


# --- Historical incidents -------------------------------------------------------------------


def incidents(city_percentile: float | None, confidence: float, note: str = "district-level official data") -> Reading:
    """Inverse of incident-density percentile (0 = lowest in city). Coarse by design."""
    if city_percentile is None:
        return NO_DATA
    return Reading(_clamp(100.0 * (1.0 - city_percentile)), confidence, note)


# --- Recent community reports ---------------------------------------------------------------

REPORT_RADIUS_M = float(_REPORTS["radius_m"])
REPORT_SEVERITY = {k: float(v) for k, v in _REPORTS["severity"].items()}  # negative = adds support
VERIFICATION_FACTOR = {k: float(v) for k, v in _REPORTS["verification_factor"].items()}
REPORT_HALF_LIFE_DAYS = float(_REPORTS["half_life_days"])
REPORT_MAX_AGE_DAYS = float(_REPORTS["max_age_days"])
REPORTS_CONFIDENCE = float(_REPORTS["confidence"])


@dataclass(frozen=True)
class Report:
    category: str
    created_at: datetime
    verification: str = "pending"


def reports(nearby: list[Report], now: datetime) -> Reading:
    """Starts at 100; each nearby report subtracts severity × verification × age decay."""
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
    # Fixed confidence: if it grew with the number of reports, a negative report would give more
    # weight to this (still fairly high) value and could *raise* a stretch's score at night.
    return Reading(_clamp(100.0 - penalty), REPORTS_CONFIDENCE, note)
