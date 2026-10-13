"""Plan a trip: find candidate walking routes, score each one, and describe why (spec §5.1-5.3, §5.5)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

from . import ncrb
from . import signals as sig
from .areas import Area, area_for
from .geo import LatLon, haversine_m, interpolate, split_polyline
from .pois import Poi, PointIndex
from .routing import AreaData, Edge, candidate_paths, load_area, path_edges
from .scoring import WEIGHTS_VERSION, SegmentScore, night_fraction, score_route, score_segment, weights_at

WALK_M_PER_MIN = 80.0  # ≈4.8 km/h
SEGMENT_M = 50.0
SAFE_SPACE_RADIUS_M = 150.0
TRANSIT_SEARCH_M = 800.0
LAMP_RADIUS_M = 25.0
REPORT_RADIUS_M = 50.0
STOP_SEARCH_M = 300.0
WEAK_SCORE = 50.0
WEAK_MIN_M = 100.0

DISCLAIMER = "Scores reflect available data and can't guarantee safety. In an emergency call 112."
SIGNAL_LABELS = {
    sig.LIGHTING: "street lighting",
    sig.TRANSIT: "transit nearby",
    sig.SAFE_SPACES: "open places nearby",
    sig.INCIDENTS: "historical incidents",
    sig.REPORTS: "recent community reports",
}
KIND_LABELS = {
    "police": "police station", "hospital": "hospital", "clinic": "clinic", "pharmacy": "pharmacy",
    "fuel": "fuel station", "convenience": "convenience store", "supermarket": "supermarket",
    "mall": "mall", "cafe": "café", "restaurant": "restaurant", "toilets": "washroom",
}


@dataclass
class TripContext:
    data: AreaData
    when: datetime  # timezone-aware departure time
    night: float
    weights: dict[str, float]
    reports: PointIndex[sig.Report] = field(default_factory=lambda: PointIndex(()))
    incidents: sig.Reading = sig.NO_DATA  # one value per area: NCRB data is district-level

    def __post_init__(self) -> None:
        hit = ncrb.lookup(self.data.area)
        if hit is not None:
            self.incidents = sig.incidents(*hit)

    @property
    def local(self) -> datetime:
        return self.when.astimezone(self.data.area.tz)


@dataclass
class PieceInfo:
    length_m: float
    points: list[LatLon]
    segment: SegmentScore
    open_places: list[Poi]


def readings_at(ctx: TripContext, p: LatLon, tags: dict | None) -> tuple[dict[str, sig.Reading], list[Poi]]:
    local = ctx.local
    lamps = len(ctx.data.lamps.within(p, LAMP_RADIUS_M))
    viirs = ctx.data.viirs.percentile_at(*p) if ctx.data.viirs is not None else None
    lighting = sig.lighting(tags, lamps, night=ctx.night >= 0.5, viirs_percentile=viirs)

    nearest_stop = None
    for d, stop in ctx.data.transit.within(p, TRANSIT_SEARCH_M):
        if stop.is_open(local)[0]:
            nearest_stop = d
            break

    open_places = [poi for _, poi in ctx.data.safe_spaces.within(p, SAFE_SPACE_RADIUS_M) if poi.is_open(local)[0]]

    nearby_reports = [r for _, r in ctx.reports.within(p, REPORT_RADIUS_M)]
    readings = {
        sig.LIGHTING: lighting,
        sig.TRANSIT: sig.transit(nearest_stop),
        sig.SAFE_SPACES: sig.safe_spaces([poi.kind for poi in open_places]),
        sig.INCIDENTS: ctx.incidents,
        sig.REPORTS: sig.reports(nearby_reports, ctx.when),
    }
    return readings, open_places


def score_pieces(ctx: TripContext, edges: list[Edge]) -> list[PieceInfo]:
    pieces = []
    for e in edges:
        for pts in split_polyline(e.points, SEGMENT_M):
            mid = interpolate(pts[0], pts[-1], 0.5)
            readings, places = readings_at(ctx, mid, e.tags)
            length = sum(haversine_m(a, b) for a, b in zip(pts, pts[1:]))
            pieces.append(PieceInfo(length, pts, score_segment(readings, ctx.weights), places))
    return pieces


@lru_cache(maxsize=16)
def _edge_support_cached(area_id: str, hour_key: str, night: float) -> dict:
    """Support score per street at a given hour, used only to steer the 'Most support' route search."""
    data = load_area(area_id)
    ctx = TripContext(data, datetime.fromisoformat(hour_key), night, weights_at(night))
    support = {}
    for u, v, d in data.graph.edges(data=True):
        key = frozenset((u, v))
        if key in support:
            continue
        a, b = ctx.data.node_latlon(u), ctx.data.node_latlon(v)
        readings, _ = readings_at(ctx, interpolate(a, b, 0.5), d)
        s = score_segment(readings, ctx.weights).score
        support[key] = 50.0 if s is None else s
    return support


def edge_support(ctx: TripContext) -> dict:
    hour = ctx.when.replace(minute=0, second=0, microsecond=0)
    return _edge_support_cached(ctx.data.area.id, hour.isoformat(), round(ctx.night, 1))


# --- Describing a route ---------------------------------------------------------------------


def weak_stretches(pieces: list[PieceInfo]) -> list[dict]:
    """Contiguous runs scoring below WEAK_SCORE, at least WEAK_MIN_M long, with the main factor."""
    out, run = [], []

    def flush():
        length = sum(p.length_m for p in run)
        if length >= WEAK_MIN_M:
            shortfall = Counter()
            for p in run:
                for k, w in p.segment.effective_weights.items():
                    shortfall[k] += w * (100 - p.segment.readings[k].value) * p.length_m
            main = [SIGNAL_LABELS[k] for k, _ in shortfall.most_common(2) if _ > 0]
            score = sum(p.segment.score * p.length_m for p in run) / length
            out.append({
                "start": run[0].points[0], "end": run[-1].points[-1], "length_m": round(length),
                "score": round(score), "main_factors": main,
            })
        run.clear()

    for p in pieces:
        if p.segment.score is not None and p.segment.score < WEAK_SCORE:
            run.append(p)
        else:
            flush()
    flush()
    return out


def stops_along(data: AreaData, line: list[LatLon], local: datetime) -> list[dict]:
    """Nearest police / washroom / hospital / pharmacy to the route, with where along it they are."""
    best: dict[str, dict] = {}
    along = 0.0
    samples = [p for piece in split_polyline(line, 25.0) for p in piece[:1]] + [line[-1]]
    for i, p in enumerate(samples):
        if i:
            along += haversine_m(samples[i - 1], p)
        for d, poi in data.stops.within(p, STOP_SEARCH_M):
            cur = best.get(poi.kind)
            if cur is None or d < cur["distance_from_route_m"] - 1:
                is_open, assumed = poi.is_open(local)
                best[poi.kind] = {
                    "kind": poi.kind, "label": KIND_LABELS.get(poi.kind, poi.kind), "name": poi.name,
                    "lat": poi.lat, "lon": poi.lon, "distance_from_route_m": round(d),
                    "along_route_m": round(along), "open": is_open, "hours_assumed": assumed, "osm": poi.osm,
                }
    order = ["police", "toilets", "hospital", "pharmacy"]
    return [best[k] for k in order if k in best]


def describe(pieces: list[PieceInfo], local: datetime) -> tuple[dict, list[str]]:
    total = sum(p.length_m for p in pieces)
    confident_lit = sum(p.length_m for p in pieces
                        if p.segment.readings[sig.LIGHTING].confidence >= 0.7
                        and p.segment.readings[sig.LIGHTING].value >= 70)
    estimated = sum(p.length_m for p in pieces if 0 < p.segment.readings[sig.LIGHTING].confidence < 0.7)
    near_transit = sum(p.length_m for p in pieces if p.segment.readings[sig.TRANSIT].value >= 99)
    places: dict[str, Poi] = {poi.osm: poi for p in pieces for poi in p.open_places}
    open_kinds = Counter(poi.kind for poi in places.values())
    assumed = sum(1 for poi in places.values() if poi.is_open(local)[1])

    highlights = {
        "lit_confirmed_share": round(confident_lit / total, 3),
        "lighting_estimated_share": round(estimated / total, 3),
        "near_transit_share": round(near_transit / total, 3),
        "open_places_passed": dict(open_kinds),
        "open_places_hours_assumed": assumed,
    }

    reasons = []
    if highlights["lit_confirmed_share"] >= 0.3:
        reasons.append(f"Mapped as lit for {highlights['lit_confirmed_share']:.0%} of the way")
    n_places = sum(open_kinds.values())
    if n_places:
        top = ", ".join(f"{n} {KIND_LABELS.get(k, k)}{'s' if n > 1 else ''}" for k, n in open_kinds.most_common(2))
        reasons.append(f"Passes {n_places} places likely open now ({top})")
    if highlights["near_transit_share"] >= 0.3:
        reasons.append(f"Within 150 m of a bus or metro stop for {highlights['near_transit_share']:.0%} of the way")
    if highlights["lighting_estimated_share"] >= 0.5:
        reasons.append("Street-light data is limited on most of this route; lighting is estimated from satellite or road type")
    return highlights, reasons


def incident_note(ctx: TripContext) -> str:
    if ctx.incidents.confidence == 0:
        return (f"No sub-city incident data for {ctx.data.area.city} (NCRB reports it as one unit); "
                "this signal's weight is shared across the others.")
    return (f"Historical incidents: {ctx.incidents.note} (NCRB). Same for every route in this area, "
            "so it doesn't change which route ranks first.")


def plan(origin: LatLon, dest: LatLon, when: datetime, reports: list[tuple[LatLon, sig.Report]] = ()) -> dict:
    if when.tzinfo is None:
        raise ValueError("departure time must be timezone-aware")
    area: Area | None = area_for(*origin)
    if area is None or not area.contains(*dest):
        raise ValueError("origin and destination must both be inside one demo area")
    data = load_area(area.id)
    night = night_fraction(when, *area.center)
    ctx = TripContext(data, when, night, weights_at(night), PointIndex(reports))

    routes = []
    for label, nodes in candidate_paths(data, origin, dest, edge_support(ctx)):
        edges = path_edges(data, nodes)
        line = [edges[0].points[0]] + [p for e in edges for p in e.points[1:]]
        pieces = score_pieces(ctx, edges)
        rs = score_route([(p.length_m, p.segment) for p in pieces], ctx.weights)
        highlights, reasons = describe(pieces, ctx.local)
        distance = sum(p.length_m for p in pieces)
        routes.append({
            "label": label,
            "distance_m": round(distance),
            "duration_min": round(distance / WALK_M_PER_MIN),
            "score": None if rs.score is None else round(rs.score),
            "band": rs.band or "Limited data",
            "coverage": round(rs.coverage, 3),
            "breakdown": [
                {"signal": k, "label": SIGNAL_LABELS[k], "value": None if v is None else round(v),
                 "confidence": round(rs.signal_confidence[k], 2), "base_weight": round(ctx.weights[k], 3)}
                for k, v in rs.signal_means.items()
            ],
            "highlights": highlights,
            "reasons": reasons,
            "weak_stretches": weak_stretches(pieces),
            "stops": stops_along(data, line, ctx.local),
            "geometry": [[round(a, 6), round(b, 6)] for a, b in line],
        })

    routes.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return {
        "area": {"id": area.id, "city": area.city, "name": area.name},
        "departure_local": ctx.local.isoformat(timespec="minutes"),
        "night_fraction": round(night, 2),
        "weights_version": WEIGHTS_VERSION,
        "routes": routes,
        "data_notes": [incident_note(ctx),
                       "Opening hours are assumed from typical hours where OpenStreetMap has none."],
        "disclaimer": DISCLAIMER,
        "attribution": "© OpenStreetMap contributors (ODbL)",
    }
