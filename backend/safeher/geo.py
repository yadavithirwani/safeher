"""Small geometry helpers. Coordinates are (lat, lon) in degrees; distances in metres."""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_008.8

LatLon = tuple[float, float]


def haversine_m(a: LatLon, b: LatLon) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def interpolate(a: LatLon, b: LatLon, t: float) -> LatLon:
    """Linear interpolation; fine at street scale."""
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def point_segment_distance_m(p: LatLon, a: LatLon, b: LatLon) -> float:
    """Distance from p to segment ab, using a local equirectangular projection around p."""
    k = math.cos(math.radians(p[0]))

    def xy(q: LatLon) -> tuple[float, float]:
        return (
            math.radians(q[1] - p[1]) * k * EARTH_RADIUS_M,
            math.radians(q[0] - p[0]) * EARTH_RADIUS_M,
        )

    ax, ay = xy(a)
    bx, by = xy(b)
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    t = 0.0 if seg_len2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg_len2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(cx, cy)


def polyline_distance_m(p: LatLon, line: list[LatLon]) -> float:
    if len(line) == 1:
        return haversine_m(p, line[0])
    return min(point_segment_distance_m(p, a, b) for a, b in zip(line, line[1:]))


@dataclass(frozen=True)
class Segment:
    """A ~50 m piece of a route. `points` includes both endpoints."""

    index: int
    points: tuple[LatLon, ...]
    length_m: float
    tags: dict | None = None  # OSM tags of the way this piece came from, if known

    @property
    def midpoint(self) -> LatLon:
        return interpolate(self.points[0], self.points[-1], 0.5)


def split_polyline(line: list[LatLon], target_m: float = 50.0) -> list[list[LatLon]]:
    """Split a polyline into consecutive pieces of ~target_m metres (last piece may be shorter)."""
    if len(line) < 2:
        raise ValueError("a route needs at least two points")
    pieces: list[list[LatLon]] = []
    current: list[LatLon] = [line[0]]
    acc = 0.0
    for a, b in zip(line, line[1:]):
        start = a
        remaining = haversine_m(a, b)
        while acc + remaining >= target_m and remaining > 0:
            need = target_m - acc
            cut = interpolate(start, b, need / remaining)
            current.append(cut)
            pieces.append(current)
            current = [cut]
            remaining -= need
            start = cut
            acc = 0.0
        if remaining > 0:
            current.append(b)
            acc += remaining
    if len(current) > 1 and acc > 1.0:
        pieces.append(current)
    elif pieces and len(current) > 1:
        pieces[-1].extend(current[1:])  # fold a tiny tail into the previous piece
    return pieces


def polyline_length_m(line: list[LatLon]) -> float:
    return sum(haversine_m(a, b) for a, b in zip(line, line[1:]))
