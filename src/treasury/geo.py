"""Pure, deterministic geometry helpers for the simulated delivery map. No I/O."""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise
from typing import NamedTuple

EARTH_RADIUS_M = 6_371_008.8
_EDGE_EPS = 1e-12


class Point(NamedTuple):
    lat: float
    lon: float


def haversine_m(a: Point, b: Point) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = p2 - p1
    dl = math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def _on_segment(p: Point, a: Point, b: Point) -> bool:
    cross = (b.lon - a.lon) * (p.lat - a.lat) - (b.lat - a.lat) * (p.lon - a.lon)
    if abs(cross) > _EDGE_EPS:
        return False
    return (
        min(a.lat, b.lat) - _EDGE_EPS <= p.lat <= max(a.lat, b.lat) + _EDGE_EPS
        and min(a.lon, b.lon) - _EDGE_EPS <= p.lon <= max(a.lon, b.lon) + _EDGE_EPS
    )


def point_in_polygon(p: Point, polygon: Sequence[Point]) -> bool:
    """Ray casting; points on the boundary count as inside. Polygon is implicitly closed."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        if _on_segment(p, a, b):
            return True
        if (a.lat > p.lat) != (b.lat > p.lat):
            x = a.lon + (p.lat - a.lat) * (b.lon - a.lon) / (b.lat - a.lat)
            if p.lon < x:
                inside = not inside
    return inside


def within_radius(p: Point, centre: Point, radius_m: float) -> bool:
    """Destination radius check (inclusive)."""
    return haversine_m(p, centre) <= radius_m


def position_fresh(position_ts: float, now: float, max_age_s: float) -> bool:
    """Fresh if not from the future and no older than max_age_s (seconds, simulated clock)."""
    age = now - position_ts
    return 0 <= age <= max_age_s


def route_length_m(route: Sequence[Point]) -> float:
    return sum(haversine_m(a, b) for a, b in pairwise(route))


def densify(route: Sequence[Point], step_m: float = 50.0) -> tuple[Point, ...]:
    """Insert points so no gap exceeds step_m, so zone crossings between vertices are caught."""
    if step_m <= 0:
        raise ValueError("step_m must be positive")
    out: list[Point] = []
    for i, a in enumerate(route):
        out.append(a)
        if i + 1 == len(route):
            break
        b = route[i + 1]
        n = max(1, math.ceil(haversine_m(a, b) / step_m))
        for k in range(1, n):
            t = k / n
            out.append(Point(a.lat + (b.lat - a.lat) * t, a.lon + (b.lon - a.lon) * t))
    return tuple(out)


def distance_to_route_m(p: Point, route: Sequence[Point]) -> float:
    """Shortest distance from p to the polyline (local equirectangular projection)."""
    if not route:
        raise ValueError("empty route")
    if len(route) == 1:
        return haversine_m(p, route[0])
    kx = math.cos(math.radians(p.lat)) * math.radians(1) * EARTH_RADIUS_M
    ky = math.radians(1) * EARTH_RADIUS_M

    def xy(q: Point) -> tuple[float, float]:
        return (q.lon - p.lon) * kx, (q.lat - p.lat) * ky

    best = math.inf
    for a, b in pairwise(route):
        ax, ay = xy(a)
        bx, by = xy(b)
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best
