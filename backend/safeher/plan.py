"""Plan a trip: find candidate walking routes, score each one, and describe why (spec §5.1-5.3, §5.5)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from . import config, store
from .backup import BackupCoverage
from . import signals as sig
from .cities import CITIES, City, corridor
from .geo import LatLon, haversine_m, interpolate, polyline_distance_m, split_polyline
from .pois import Poi, PointIndex
from .routing import AreaData, Edge, candidate_paths, edge_between, path_edges
from .scoring import WEIGHTS_VERSION, SegmentScore, band, night_fraction, score_route, score_segment, weights_at

_C = config.scoring()
_R, _REASONS, _LIGHT = _C["routing"], _C["reasons"], _C["lighting"]
WALK_M_PER_MIN = float(_R["walk_m_per_min"])
SEGMENT_M = float(_R["segment_m"])
STOP_SEARCH_M = float(_R["stop_search_m"])
STOP_SAMPLE_M = float(_R["stop_sample_m"])
WEAK_SCORE = float(_R["weak_stretch_score_below"])
WEAK_MIN_M = float(_R["weak_stretch_min_m"])
UNSCORED_STREET_SUPPORT = float(_R["unscored_street_support"])
# Lighting counts as "mapped" from lamp-level evidence up, and as "measured" (shown on the map)
# from satellite-level evidence up; both thresholds follow the lighting confidences in config.
MAPPED_LIGHT_CONFIDENCE = float(_LIGHT["lamp_confidence"])
MEASURED_LIGHT_CONFIDENCE = float(_LIGHT["viirs_confidence"])
TRANSIT_FULL_M = float(_C["transit"]["full_score_within_m"])

DISCLAIMER = config.app()["disclaimer"]
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
    # NCRB is per police district: looked up for each point, cached at ~10 m, and every district the
    # scored routes touch is remembered for the trip's data note.
    _incidents: dict = field(default_factory=dict, repr=False)
    seen_districts: dict[str, None] = field(default_factory=dict, repr=False)

    @property
    def city(self) -> City:
        return CITIES[self.data.area.city]

    def incident_at(self, p: LatLon) -> sig.Reading:
        key = (round(p[0], 4), round(p[1], 4))
        r = self._incidents.get(key)
        if r is None:
            hit = store.incident_at(self.city, p)
            r = self._incidents[key] = sig.incidents(*hit) if hit else sig.NO_DATA
        return r

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
    lamps = len(ctx.data.lamps.within(p, sig.LAMP_RADIUS_M))
    viirs = ctx.data.viirs.percentile_at(*p) if ctx.data.viirs is not None else None
    lighting = sig.lighting(tags, lamps, night=ctx.night >= 0.5, viirs_percentile=viirs)

    nearest_stop = None
    for d, stop in ctx.data.transit.within(p, sig.TRANSIT_SEARCH_M):
        if stop.is_open(local)[0]:
            nearest_stop = d
            break

    open_places = [poi for _, poi in ctx.data.safe_spaces.within(p, sig.SAFE_SPACE_RADIUS_M) if poi.is_open(local)[0]]

    nearby_reports = [r for _, r in ctx.reports.within(p, sig.REPORT_RADIUS_M)]
    readings = {
        sig.LIGHTING: lighting,
        sig.TRANSIT: sig.transit(nearest_stop),
        sig.SAFE_SPACES: sig.safe_spaces([poi.kind for poi in open_places]),
        sig.INCIDENTS: ctx.incident_at(p),
        sig.REPORTS: sig.reports(nearby_reports, ctx.when),
    }
    return readings, open_places


def score_pieces(ctx: TripContext, edges: list[Edge]) -> list[PieceInfo]:
    pieces = []
    for e in edges:
        for pts in split_polyline(e.points, SEGMENT_M):
            mid = interpolate(pts[0], pts[-1], 0.5)
            readings, places = readings_at(ctx, mid, e.tags)
            if readings[sig.INCIDENTS].confidence > 0:
                ctx.seen_districts[readings[sig.INCIDENTS].note] = None
            length = sum(haversine_m(a, b) for a, b in zip(pts, pts[1:]))
            pieces.append(PieceInfo(length, pts, score_segment(readings, ctx.weights), places))
    return pieces


_SUPPORT_CACHE: dict[tuple, dict] = {}
_SUPPORT_CACHE_MAX = 16


def edge_support(ctx: TripContext, reports_key: int = 0) -> dict:
    """Support score per street at the departure hour, used only to steer the 'Most support' search.

    Cached per (area, hour, reports); a new community report invalidates it.
    """
    hour = ctx.when.replace(minute=0, second=0, microsecond=0)
    key = (ctx.data.area.id, hour.isoformat(), round(ctx.night, 1), reports_key)
    if key in _SUPPORT_CACHE:
        return _SUPPORT_CACHE[key]
    hour_ctx = TripContext(ctx.data, hour, ctx.night, ctx.weights, ctx.reports)
    support = {}
    for u, v, d in ctx.data.graph.edges(data=True):
        k = frozenset((u, v))
        if k in support:
            continue
        a, b = ctx.data.node_latlon(u), ctx.data.node_latlon(v)
        readings, _ = readings_at(hour_ctx, interpolate(a, b, 0.5), d)
        s = score_segment(readings, ctx.weights).score
        support[k] = UNSCORED_STREET_SUPPORT if s is None else s
    if len(_SUPPORT_CACHE) >= _SUPPORT_CACHE_MAX:
        _SUPPORT_CACHE.pop(next(iter(_SUPPORT_CACHE)))
    _SUPPORT_CACHE[key] = support
    return support


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
                "start": [round(c, 6) for c in run[0].points[0]],
                "end": [round(c, 6) for c in run[-1].points[-1]], "length_m": round(length),
                "score": round(score), "main_factors": main,
            })
        run.clear()

    for p in pieces:
        if p.segment.score is not None and round(p.segment.score) < WEAK_SCORE:
            run.append(p)
        else:
            flush()
    flush()
    return out


def stops_along(data: AreaData, line: list[LatLon], local: datetime) -> list[dict]:
    """Nearest police / washroom / hospital / pharmacy to the route, with where along it they are."""
    best: dict[str, dict] = {}
    along = 0.0
    samples = [p for piece in split_polyline(line, STOP_SAMPLE_M) for p in piece[:1]] + [line[-1]]
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
                        if p.segment.readings[sig.LIGHTING].confidence >= MAPPED_LIGHT_CONFIDENCE
                        and p.segment.readings[sig.LIGHTING].value >= _REASONS["lit_value_at_least"])
    estimated = sum(p.length_m for p in pieces if 0 < p.segment.readings[sig.LIGHTING].confidence < MAPPED_LIGHT_CONFIDENCE)
    near_transit = sum(p.length_m for p in pieces if p.segment.readings[sig.TRANSIT].value >= 100)
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
    if highlights["lit_confirmed_share"] >= _REASONS["lit_min_share"]:
        reasons.append(f"Mapped as lit for {highlights['lit_confirmed_share']:.0%} of the way")
    n_places = sum(open_kinds.values())
    if n_places:
        top = ", ".join(f"{n} {KIND_LABELS.get(k, k)}{'s' if n > 1 else ''}" for k, n in open_kinds.most_common(2))
        reasons.append(f"Passes {n_places} places likely open now ({top})")
    if highlights["near_transit_share"] >= _REASONS["near_transit_min_share"]:
        reasons.append(f"Within {TRANSIT_FULL_M:.0f} m of a bus or metro stop for {highlights['near_transit_share']:.0%} of the way")
    if highlights["lighting_estimated_share"] >= _REASONS["estimated_lighting_note_share"]:
        reasons.append("Street-light data is limited on most of this route; lighting is estimated from satellite or road type")
    return highlights, reasons


def incident_note(ctx: TripContext) -> str:
    if not ctx.city.districts:
        return (f"No sub-city incident data for {ctx.city.name} (NCRB reports it as one unit); "
                "this signal's weight is shared across the others.")
    if not ctx.seen_districts:
        return (f"No NCRB police-district figures for this part of {ctx.city.name}; "
                "this signal's weight is shared across the others.")
    return ("Historical incidents (NCRB, by police district along the routes): "
            + "; ".join(ctx.seen_districts) + ".")


# --- External routes (e.g. Google): scored by the same engine ----------------------------------

_EDGE_SAMPLES: dict[str, PointIndex] = {}
EDGE_SAMPLE_M = 15.0
SAME_ROUTE_M = 20.0  # an external route within this distance of one of ours is "the same street"
SAME_ROUTE_SHARE = 0.85


def _edge_samples(data: AreaData) -> PointIndex:
    """Points every ~15 m along every street, carrying that street's OSM tags (built once per area)."""
    idx = _EDGE_SAMPLES.get(data.area.id)
    if idx is None:
        items = []
        for u, v, d in data.graph.edges(data=True):
            e = edge_between(data, u, v)
            for piece in split_polyline(e.points, EDGE_SAMPLE_M) if len(e.points) > 1 else []:
                items.append((interpolate(piece[0], piece[-1], 0.5), e.tags))
        idx = _EDGE_SAMPLES[data.area.id] = PointIndex(items)
    return idx


def score_polyline(ctx: TripContext, points: list[LatLon], match_m: float) -> tuple[list[PieceInfo], float]:
    """Score any walking polyline. Each ~50 m piece takes the tags of the nearest mapped street
    within `match_m`; unmatched pieces get no tags (lighting then falls back to satellite/no data).
    Returns (pieces, share of length matched to a mapped street)."""
    samples = _edge_samples(ctx.data)
    pieces, matched = [], 0.0
    for pts in split_polyline(points, SEGMENT_M):
        mid = interpolate(pts[0], pts[-1], 0.5)
        hit = samples.nearest(mid, match_m)
        tags = hit[1] if hit else None
        readings, places = readings_at(ctx, mid, tags)
        length = sum(haversine_m(a, b) for a, b in zip(pts, pts[1:]))
        matched += length if hit else 0.0
        pieces.append(PieceInfo(length, pts, score_segment(readings, ctx.weights), places))
    total = sum(pc.length_m for pc in pieces) or 1.0
    return pieces, matched / total


def _same_as(points: list[LatLon], line: list[LatLon]) -> float:
    """Share of `points`' length lying within SAME_ROUTE_M of `line`."""
    total = near = 0.0
    for pts in split_polyline(points, SEGMENT_M):
        length = sum(haversine_m(a, b) for a, b in zip(pts, pts[1:]))
        total += length
        if polyline_distance_m(interpolate(pts[0], pts[-1], 0.5), line) <= SAME_ROUTE_M:
            near += length
    return near / total if total else 0.0


def _route_payload(ctx: TripContext, label: str, pieces: list[PieceInfo], line: list[LatLon],
                   plan_b: dict | None, source: str) -> dict:
    rs = score_route([(p.length_m, p.segment) for p in pieces], ctx.weights)
    highlights, reasons = describe(pieces, ctx.local)
    distance = sum(p.length_m for p in pieces)
    return {
        "plan_b": plan_b,
        "label": label,
        "source": source,
        "distance_m": round(distance),
        "duration_min": round(round(distance) / WALK_M_PER_MIN),  # from the distance shown
        "score": None if rs.score is None else round(rs.score),
        "band": "Limited data" if rs.score is None else band(round(rs.score)),
        "coverage": round(rs.coverage, 3),
        "breakdown": [
            {"signal": k, "label": SIGNAL_LABELS[k], "value": None if v is None else round(v),
             "confidence": round(rs.signal_confidence[k], 2), "base_weight": round(ctx.weights[k], 3)}
            for k, v in rs.signal_means.items()
        ],
        "highlights": highlights,
        "reasons": reasons,
        "weak_stretches": weak_stretches(pieces),
        "stops": stops_along(ctx.data, line, ctx.local),
        "geometry": [[round(a, 6), round(b, 6)] for a, b in line],
        "pieces": [{"coords": [[round(a, 6), round(b, 6)] for a, b in pc.points],
                    "score": None if pc.segment.score is None else round(pc.segment.score),
                    # Lighting shown on the map only when it's measured (tag, lamps or satellite).
                    "light": (round(pc.segment.readings[sig.LIGHTING].value)
                              if pc.segment.readings[sig.LIGHTING].confidence >= MEASURED_LIGHT_CONFIDENCE else None)}
                   for pc in pieces],
    }


def plan(origin: LatLon, dest: LatLon, when: datetime, reports: list[tuple[LatLon, sig.Report]] = (),
         external_routes: list[dict] = (), external_match_m: float = 25.0) -> dict:
    """Plan and score walking routes.

    external_routes: [{"label", "source", "points": [(lat, lon), …]}] from another router (Google).
    They are scored by the same engine; one that follows an existing route is folded into it
    (that route gets `also_<source>: true`) instead of being listed twice.
    """
    if when.tzinfo is None:
        raise ValueError("departure time must be timezone-aware")
    area = corridor(origin, dest)  # raises cities.Unsupported outside supported cities
    data = store.corridor_data(area)
    night = night_fraction(when, *area.center)
    ctx = TripContext(data, when, night, weights_at(night), PointIndex(reports))
    reports_key = hash(tuple((p, r.category, r.created_at, r.verification) for p, r in reports))

    backup = BackupCoverage(data, when)
    scored: dict[tuple, list[PieceInfo]] = {}

    def pieces_for(nodes: list[int]) -> list[PieceInfo]:
        key = tuple(nodes)
        if key not in scored:
            scored[key] = score_pieces(ctx, path_edges(data, nodes))
        return scored[key]

    def route_score_of(nodes: list[int]) -> float:
        rs = score_route([(p.length_m, p.segment) for p in pieces_for(nodes)], ctx.weights)
        return -1.0 if rs.score is None else rs.score

    routes, lines = [], []
    for label, nodes in candidate_paths(data, origin, dest, edge_support(ctx, reports_key), rank=route_score_of):
        edges = path_edges(data, nodes)
        line = [edges[0].points[0]] + [p for e in edges for p in e.points[1:]]
        routes.append(_route_payload(ctx, label, pieces_for(nodes), line, backup.describe(edges), "safeher"))
        lines.append(line)

    notes = []
    for ext in external_routes:
        pts = [tuple(p) for p in ext["points"]]
        if len(pts) < 2 or not all(area.contains(*p) for p in pts):
            notes.append(f"A {ext['source']} route leaves this trip's street data, so it isn't scored.")
            continue
        same = next((i for i, line in enumerate(lines) if _same_as(pts, line) >= SAME_ROUTE_SHARE), None)
        if same is not None:
            routes[same][f"also_{ext['source']}"] = True
            continue
        pieces, matched = score_polyline(ctx, pts, external_match_m)
        r = _route_payload(ctx, ext["label"], pieces, pts, None, ext["source"])
        r["matched_share"] = round(matched, 3)
        routes.append(r)
        lines.append(pts)

    routes.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return {
        "area": {"id": area.id, "city": area.city, "name": area.name},
        "departure_local": ctx.local.isoformat(timespec="minutes"),
        "night_fraction": round(night, 2),
        "weights_version": WEIGHTS_VERSION,
        "routes": routes,
        "data_notes": [incident_note(ctx),
                       "Opening hours are assumed from typical hours where OpenStreetMap has none."] + notes,
        "disclaimer": DISCLAIMER,
        "attribution": "© OpenStreetMap contributors (ODbL)",
    }
