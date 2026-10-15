from dataclasses import replace

import pytest

from treasury.geo import Point
from treasury.planner import (
    Action,
    Coverage,
    Driver,
    Job,
    Route,
    World,
    WorldEvent,
    Zone,
    apply_event,
    build_actions,
    make_plan,
    payment_commands,
    replan,
)


def box(lat0, lat1, lon0, lon1):
    return (Point(lat0, lon0), Point(lat0, lon1), Point(lat1, lon1), Point(lat1, lon0))


N = Zone("N", box(51.500, 51.510, -0.12, -0.08), "permitted")
S = Zone("S", box(51.510, 51.520, -0.12, -0.08), "permitted")
X = Zone("X", box(51.515, 51.518, -0.10, -0.09), "prohibited")
R1 = Route("R1", (Point(51.502, -0.11), Point(51.508, -0.09)))
R1_INTO_S = (Point(51.502, -0.11), Point(51.512, -0.105))
R2 = Route("R2", (Point(51.501, -0.115), Point(51.509, -0.085)))
JOB = Job(
    value_minor=3000,
    start_ts=0,
    expected_duration_s=3600,
    merchant_minor=2500,
    driver_fee_minor=700,
)
COV_A = Coverage("A", ("parcel",), ("van",), 10_000, ("N",), 10_000)
COV_B = Coverage("B", ("parcel",), ("van",), 10_000, ("N", "S"), 10_000)


def world(routes=(R1,), zones=(N, S, X), cov=(COV_A, COV_B)):
    return World(
        JOB,
        (Driver("A", eta_s=60), Driver("B", eta_s=600)),
        tuple(cov),
        tuple(zones),
        tuple(routes),
    )


def test_initial_plan_goal_and_cheapest_driver():
    p = make_plan(world())
    assert p.status == "planned"
    assert p.step_names == (
        "select_route:R1",
        "assign:A:R1",
        "pickup",
        "deliver",
        "confirm_receipt",
        "pay_driver",
        "pay_merchant",
    )
    assert p.assignment_version == 1 and p.driver_id == "A"
    assert make_plan(world()) == p  # deterministic


def test_coverage_change_replans_remaining_actions():
    w = world()
    p1 = make_plan(w)
    done = ("select_route:R1", "assign:A:R1")
    w2 = apply_event(
        w, WorldEvent("route_changed", {"route_id": "R1", "points": R1_INTO_S})
    )
    p2, change = replan(p1, w2, done)
    assert p2.status == "planned"
    assert p2.driver_id == "B" and p2.assignment_version == 2
    assert "assign:A:R1" not in p2.completed and "select_route:R1" in p2.completed
    assert "assign:B:R1" in p2.step_names
    assert change.cause_clauses == ("COVER-1",)
    assert "assign:A:R1" in change.removed and "assign:B:R1" in change.added
    fuel = next(e for e in p2.entitlements if e["type"] == "fuel")
    assert (
        fuel["driver_id"] == "B"
        and fuel["assignment_version"] == 2
        and fuel["status"] == "pending"
    )
    assert all(e["assignment_version"] == 2 for e in p2.entitlements)


def test_alternative_route_allowed():
    w = world(routes=(R1, R2), cov=(COV_A,))
    w = replace(w, drivers=(Driver("A", eta_s=60),))
    p1 = make_plan(w)
    w2 = apply_event(
        w, WorldEvent("route_changed", {"route_id": "R1", "points": R1_INTO_S})
    )
    p2, _ = replan(p1, w2, ("select_route:R1", "assign:A:R1"))
    assert p2.status == "planned"
    assert p2.route_id == "R2" and p2.driver_id == "A"


def test_no_permitted_plan_does_not_relax_policy():
    # route crosses X; cover includes X, but org exclusion still wins
    cov = Coverage("A", ("parcel",), ("van",), 10_000, ("N", "S", "X"), 10_000)
    w = world(cov=(cov,))
    w = replace(w, drivers=(Driver("A"),))
    w = apply_event(
        w,
        WorldEvent(
            "route_changed",
            {
                "route_id": "R1",
                "points": (Point(51.502, -0.095), Point(51.517, -0.095)),
            },
        ),
    )
    p = make_plan(w)
    assert p.status == "refused" and p.resolution == "cancel" and not p.policy_weakened
    assert p.steps == () and payment_commands(p) == []
    assert any(r["clause"] == "TRAVEL-1" and "X" in r["detail"] for r in p.reasons)


def test_coverage_requires_job_vehicle_value_duration():
    base = world(cov=(COV_A,))
    base = replace(base, drivers=(Driver("A"),))
    for cov, frag in [
        (replace(COV_A, job_types=("food",)), "job type"),
        (replace(COV_A, vehicles=("bike",)), "vehicle"),
        (replace(COV_A, max_value_minor=100), "value"),
        (replace(COV_A, valid_until=100), "expires"),
    ]:
        p = make_plan(replace(base, coverage=(cov,)))
        assert p.status == "refused"
        assert any(frag in r["detail"] for r in p.reasons), frag


def test_completed_operations_survive_replanning():
    w = world(cov=(COV_B,))
    p1 = make_plan(w)
    done = (
        "select_route:R1",
        "assign:B:R1",
        "pickup",
        "deliver",
        "confirm_receipt",
        "pay_merchant",
    )
    # coverage for B changes mid-trip, after pickup: nothing is reassigned, money is not undone
    w2 = apply_event(
        w, WorldEvent("coverage_changed", {"coverage": replace(COV_B, zone_ids=("S",))})
    )
    p2, _ = replan(p1, w2, done)
    assert p2.status == "refused" and p2.resolution == "escalate"
    assert set(done[2:]) <= set(
        p2.completed
    )  # money/physical ops stay; revocable assign dropped
    assert "assign:B:R1" not in p2.completed
    # unaffected change: pending pay_driver remains, confirmed merchant payment preserved
    w3 = apply_event(w, WorldEvent("position", {"point": Point(51.5, -0.1), "ts": 5}))
    p3, ch3 = replan(make_plan(w, done), w3)
    assert p3.step_names == ("pay_driver",) and "pay_merchant" in p3.completed
    merchant = next(e for e in p3.entitlements if e["type"] == "merchant_payment")
    assert merchant["status"] == "confirmed"
    assert [c["op"] for c in payment_commands(p3)] == ["pay_driver"]
    assert ch3.kept_completed == done


def test_replay_generates_no_payment_commands():
    p = make_plan(world())
    assert len(payment_commands(p)) == 2
    assert payment_commands(p, replay=True) == []


def test_idempotency_key_stable_across_replans():
    w = world()
    p1 = make_plan(w)
    p2, _ = replan(
        p1,
        apply_event(w, WorldEvent("position", {"point": Point(51.5, -0.1), "ts": 1})),
    )
    assert payment_commands(p1) == payment_commands(p2)


def test_events_must_be_labelled_simulated():
    with pytest.raises(ValueError):
        apply_event(
            world(),
            WorldEvent("position", {"point": Point(0, 0), "ts": 0}, label="live"),
        )


def test_negative_cost_rejected_and_search_bound(monkeypatch):
    with pytest.raises(ValueError):
        Action("bad", frozenset(), frozenset({"x"}), -1)
    import treasury.planner as pl

    monkeypatch.setattr(pl, "MAX_EXPANSIONS", 0)
    p = make_plan(world())
    assert p.status == "refused" and any(
        r["clause"] == "SEARCH-BOUND" for r in p.reasons
    )
    assert build_actions(world())


def test_unapproved_driver_never_assigned():
    w = replace(world(cov=(COV_A,)), drivers=(Driver("A", approved=False),))
    p = make_plan(w)
    assert p.status == "refused" and any(r["clause"] == "DRIVER-1" for r in p.reasons)
