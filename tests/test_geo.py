from itertools import pairwise

from treasury.geo import (
    Point,
    densify,
    distance_to_route_m,
    haversine_m,
    point_in_polygon,
    position_fresh,
    route_length_m,
    within_radius,
)

SQ = (Point(0, 0), Point(0, 1), Point(1, 1), Point(1, 0))


def test_haversine_known_distance():
    # one degree of latitude is about 111.19 km
    assert abs(haversine_m(Point(0, 0), Point(1, 0)) - 111_195) < 50
    assert haversine_m(Point(51.5, -0.1), Point(51.5, -0.1)) == 0


def test_point_in_polygon_inside_outside_boundary():
    assert point_in_polygon(Point(0.5, 0.5), SQ)
    assert not point_in_polygon(Point(1.5, 0.5), SQ)
    assert point_in_polygon(Point(0, 0.5), SQ)  # edge
    assert point_in_polygon(Point(1, 1), SQ)  # vertex
    assert not point_in_polygon(Point(0.5, 0.5), SQ[:2])


def test_point_in_concave_polygon():
    c = (Point(0, 0), Point(0, 2), Point(1, 1), Point(2, 2), Point(2, 0))
    assert point_in_polygon(Point(0.5, 1.0), c)
    assert not point_in_polygon(Point(1.8, 1.9), c)


def test_destination_radius_inclusive():
    c = Point(51.5, -0.1)
    near = Point(51.5, -0.1 + 0.0005)
    d = haversine_m(near, c)
    assert within_radius(near, c, d)
    assert not within_radius(near, c, d - 1)


def test_position_freshness():
    assert position_fresh(100, 110, 30)
    assert position_fresh(100, 130, 30)
    assert not position_fresh(100, 131, 30)
    assert not position_fresh(120, 110, 30)  # future timestamp is never fresh


def test_densify_and_length():
    r = (Point(51.5, -0.1), Point(51.5, -0.09))
    pts = densify(r, 50)
    assert pts[0] == r[0] and pts[-1] == r[1]
    assert all(haversine_m(a, b) <= 50.5 for a, b in pairwise(pts))
    assert abs(route_length_m(pts) - route_length_m(r)) < 1


def test_distance_to_route():
    r = (Point(51.5, -0.10), Point(51.5, -0.09))
    off = Point(51.5009, -0.095)  # about 100 m north of the line
    assert 95 < distance_to_route_m(off, r) < 105
    assert (
        distance_to_route_m(Point(51.5, -0.11), r) > 600
    )  # beyond the end, measured to endpoint
