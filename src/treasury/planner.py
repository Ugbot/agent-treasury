"""Basic deterministic GOAP planner for the delivery demo. Pure; no I/O.

World facts (positions, coverage, zones, routes) are inputs. Policy checks (COVER-1,
TRAVEL-1, DRIVER-1) decide which actions exist at all; the planner never relaxes them.
If no compliant plan exists the result is a refusal with an escalate/cancel resolution.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field, replace
from typing import Literal

from .geo import Point, densify, point_in_polygon, route_length_m

GOAL = frozenset({"delivered", "merchant_paid", "driver_paid"})
MAX_EXPANSIONS = 5_000

# ----------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class Zone:
    id: str
    polygon: tuple[Point, ...]
    kind: Literal["permitted", "prohibited"]


@dataclass(frozen=True)
class Coverage:
    """Supplied (simulated) demo insurance record."""

    driver_id: str
    job_types: tuple[str, ...]
    vehicles: tuple[str, ...]
    max_value_minor: int
    zone_ids: tuple[str, ...]
    valid_until: float


@dataclass(frozen=True)
class Driver:
    id: str
    approved: bool = True
    eta_s: int = 0
    vehicle: str = "van"


@dataclass(frozen=True)
class Route:
    id: str
    points: tuple[Point, ...]


@dataclass(frozen=True)
class Job:
    job_type: str = "parcel"
    vehicle: str = "van"
    value_minor: int = 0
    start_ts: float = 0.0
    expected_duration_s: float = 0.0
    merchant_minor: int = 0
    driver_fee_minor: int = 0
    fuel_cap_minor: int = 800


@dataclass(frozen=True)
class World:
    job: Job
    drivers: tuple[Driver, ...]
    coverage: tuple[Coverage, ...]
    zones: tuple[Zone, ...]
    routes: tuple[Route, ...]  # candidate routes, proposed first
    revision: int = 0
    position: Point | None = None
    position_ts: float | None = None


@dataclass(frozen=True)
class WorldEvent:
    """A labelled simulated map event. It changes inputs only; it never authorises money."""

    kind: Literal["route_changed", "coverage_changed", "zones_changed", "position"]
    payload: dict = field(default_factory=dict)
    label: str = "simulated"


def apply_event(world: World, ev: WorldEvent) -> World:
    if ev.label != "simulated":
        raise ValueError("map events must be labelled 'simulated'")
    p = ev.payload
    rev = world.revision + 1
    if ev.kind == "route_changed":
        route = Route(p["route_id"], tuple(p["points"]))
        others = tuple(r for r in world.routes if r.id != route.id)
        existing = [r.id for r in world.routes]
        routes = (
            tuple(route if r.id == route.id else r for r in world.routes)
            if route.id in existing
            else others + (route,)
        )
        return replace(world, routes=routes, revision=rev)
    if ev.kind == "coverage_changed":
        rec = p["coverage"]
        cov = tuple(c for c in world.coverage if c.driver_id != rec.driver_id) + (rec,)
        return replace(world, coverage=cov, revision=rev)
    if ev.kind == "zones_changed":
        return replace(world, zones=tuple(p["zones"]), revision=rev)
    if ev.kind == "position":
        return replace(world, position=p["point"], position_ts=p["ts"], revision=rev)
    raise ValueError(f"unknown event kind {ev.kind}")


# ----------------------------------------------------------------------------- policy checks


def route_zone_ids(world: World, route: Route) -> tuple[str, ...]:
    pts = densify(route.points)
    return tuple(
        sorted(
            z.id
            for z in world.zones
            if any(point_in_polygon(q, z.polygon) for q in pts)
        )
    )


def travel_violations(world: World, route: Route) -> list[dict]:
    """TRAVEL-1: no prohibited zone entered; every point inside some permitted zone."""
    pts = densify(route.points)
    out: list[dict] = []
    for z in world.zones:
        if z.kind == "prohibited" and any(point_in_polygon(q, z.polygon) for q in pts):
            out.append(
                {
                    "clause": "TRAVEL-1",
                    "route": route.id,
                    "detail": f"enters prohibited zone {z.id}",
                }
            )
    permitted = [z for z in world.zones if z.kind == "permitted"]
    if any(not any(point_in_polygon(q, z.polygon) for z in permitted) for q in pts):
        out.append(
            {
                "clause": "TRAVEL-1",
                "route": route.id,
                "detail": "leaves permitted zones",
            }
        )
    return out


def coverage_violations(world: World, driver: Driver, route: Route) -> list[dict]:
    """DRIVER-1 and COVER-1 for one driver on one route. Org exclusions are not consulted here
    so cover can never override them (TRAVEL-1 is checked separately and must also pass)."""
    out: list[dict] = []

    def bad(clause: str, detail: str) -> None:
        out.append(
            {"clause": clause, "driver": driver.id, "route": route.id, "detail": detail}
        )

    if not driver.approved:
        bad("DRIVER-1", "driver not approved")
    cov = next((c for c in world.coverage if c.driver_id == driver.id), None)
    job = world.job
    if cov is None:
        bad("COVER-1", "no coverage record")
        return out
    if job.job_type not in cov.job_types:
        bad("COVER-1", f"job type {job.job_type} not covered")
    if job.vehicle not in cov.vehicles:
        bad("COVER-1", f"vehicle {job.vehicle} not covered")
    if job.value_minor > cov.max_value_minor:
        bad("COVER-1", "goods value above cover")
    missing = [z for z in route_zone_ids(world, route) if z not in cov.zone_ids]
    if missing:
        bad("COVER-1", f"zones not covered: {','.join(missing)}")
    if cov.valid_until < job.start_ts + job.expected_duration_s:
        bad("COVER-1", "cover expires before expected completion")
    return out


def check_candidate(world: World, driver: Driver, route: Route) -> list[dict]:
    return travel_violations(world, route) + coverage_violations(world, driver, route)


# ----------------------------------------------------------------------------- GOAP core


@dataclass(frozen=True)
class Action:
    name: str
    pre: frozenset[str]
    add: frozenset[str]
    cost: int
    forbid: frozenset[str] = frozenset()
    irreversible: bool = False
    gate: str | None = None

    def __post_init__(self) -> None:
        if self.cost < 0:
            raise ValueError(f"action {self.name} has negative cost")

    def applicable(self, state: frozenset[str]) -> bool:
        return self.pre <= state and not (self.forbid & state)

    def apply(self, state: frozenset[str]) -> frozenset[str]:
        return state | self.add


def _fs(*xs: str) -> frozenset[str]:
    return frozenset(xs)


def build_actions(world: World) -> dict[str, Action]:
    acts: list[Action] = []
    for r in world.routes:
        acts.append(
            Action(
                f"select_route:{r.id}",
                _fs(f"permitted:{r.id}"),
                _fs("route_chosen", f"route:{r.id}"),
                1 + int(route_length_m(r.points) // 100),
                forbid=_fs("route_chosen"),
            )
        )
        for d in world.drivers:
            acts.append(
                Action(
                    f"assign:{d.id}:{r.id}",
                    _fs(f"route:{r.id}", f"covered:{d.id}:{r.id}", f"approved:{d.id}"),
                    _fs("driver_assigned", "assignment_valid"),
                    1 + d.eta_s // 60,
                    forbid=_fs("driver_assigned"),
                )
            )
    acts += [
        Action(
            "pickup",
            _fs("assignment_valid"),
            _fs("picked_up"),
            1,
            forbid=_fs("picked_up"),
            irreversible=True,
        ),
        Action(
            "deliver",
            _fs("picked_up", "assignment_valid"),
            _fs("delivered"),
            1,
            irreversible=True,
        ),
        Action(
            "confirm_receipt",
            _fs("delivered"),
            _fs("receipt_confirmed"),
            1,
            irreversible=True,
            gate="RECEIPT-1: fresh position in destination radius + one-use recipient confirmation",
        ),
        Action(
            "pay_merchant",
            _fs("receipt_confirmed", "assignment_valid"),
            _fs("merchant_paid"),
            1,
            irreversible=True,
        ),
        Action(
            "pay_driver",
            _fs("receipt_confirmed", "assignment_valid"),
            _fs("driver_paid"),
            1,
            irreversible=True,
        ),
    ]
    return {a.name: a for a in sorted(acts, key=lambda a: a.name)}


def static_facts(world: World) -> frozenset[str]:
    facts: set[str] = set()
    for r in world.routes:
        if not travel_violations(world, r):
            facts.add(f"permitted:{r.id}")
        for d in world.drivers:
            if d.approved:
                facts.add(f"approved:{d.id}")
            if not coverage_violations(world, d, r):
                facts.add(f"covered:{d.id}:{r.id}")
    return frozenset(facts)


def _heuristic(state: frozenset[str], acts: list[Action]) -> int:
    """Admissible: max over unmet goal facts of the cheapest action that adds it."""
    h = 0
    for g in GOAL - state:
        costs = [a.cost for a in acts if g in a.add]
        h = max(h, min(costs) if costs else 0)
    return h


def astar(
    start: frozenset[str], acts: dict[str, Action], max_expansions: int | None = None
):
    """Returns (names, cost) or (None, reason). Ties break on lexicographic action-name path."""
    max_expansions = MAX_EXPANSIONS if max_expansions is None else max_expansions
    alist = list(acts.values())
    heap: list[tuple[int, int, tuple[str, ...], frozenset[str]]] = [
        (_heuristic(start, alist), 0, (), start)
    ]
    best: dict[frozenset[str], int] = {start: 0}
    expansions = 0
    while heap:
        _f, g, path, state = heapq.heappop(heap)
        if GOAL <= state:
            return path, g
        if g > best.get(state, g):
            continue
        expansions += 1
        if expansions > max_expansions:
            return None, "search_bound"
        for a in acts.values():
            if not a.applicable(state):
                continue
            ns, ng = a.apply(state), g + a.cost
            if ng < best.get(ns, 1 << 60):
                best[ns] = ng
                heapq.heappush(
                    heap, (ng + _heuristic(ns, alist), ng, path + (a.name,), ns)
                )
    return None, "no_plan"


# ----------------------------------------------------------------------------- plans


@dataclass(frozen=True)
class Step:
    name: str
    cost: int
    gate: str | None = None


@dataclass(frozen=True)
class Plan:
    status: Literal["planned", "refused"]
    completed: tuple[str, ...]
    steps: tuple[Step, ...]
    total_cost: int
    driver_id: str | None
    route_id: str | None
    assignment_version: int
    entitlements: tuple[dict, ...]
    reasons: tuple[dict, ...] = ()
    resolution: str | None = None  # escalate | cancel when refused
    policy_weakened: bool = False
    world_revision: int = 0

    @property
    def step_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.steps)


@dataclass(frozen=True)
class PlanChange:
    old_steps: tuple[str, ...]
    new_steps: tuple[str, ...]
    removed: tuple[str, ...]
    added: tuple[str, ...]
    kept_completed: tuple[str, ...]
    cause_clauses: tuple[str, ...]
    causes: tuple[dict, ...]


def _restore_state(world: World, completed: tuple[str, ...], acts: dict[str, Action]):
    """Apply confirmed effects. Irreversible ones always stay; revocable ones only while still valid."""
    state = static_facts(world)
    kept: list[str] = []
    for name in completed:
        a = acts.get(name)
        if a is None:
            # Unknown in the new world: only physical/irreversible, parameterless ops survive.
            if name in _IRREVERSIBLE_FALLBACK:
                state |= _IRREVERSIBLE_FALLBACK[name]
                kept.append(name)
            continue
        if a.irreversible or a.applicable(state):
            state = a.apply(state)
            kept.append(name)
    if "assignment_valid" in state:
        # Assignment survives only if the assigned (driver, route) is still compliant.
        assigned = [n for n in kept if n.startswith("assign:")]
        routes = [n.split(":")[1] for n in kept if n.startswith("select_route:")]
        if not assigned or not routes:
            state -= {"assignment_valid"}
    return state, tuple(kept)


_IRREVERSIBLE_FALLBACK = {
    "pickup": _fs("picked_up"),
    "deliver": _fs("delivered"),
    "confirm_receipt": _fs("receipt_confirmed"),
    "pay_merchant": _fs("merchant_paid"),
    "pay_driver": _fs("driver_paid"),
}


def _entitlements(
    world: World, driver_id: str | None, version: int, done: set[str]
) -> tuple[dict, ...]:
    if driver_id is None:
        return ()
    j = world.job
    paid_m, paid_d = "pay_merchant" in done, "pay_driver" in done
    return (
        {
            "type": "fuel",
            "driver_id": driver_id,
            "cap_minor": j.fuel_cap_minor,
            "status": "closed" if "deliver" in done else "pending",
            "assignment_version": version,
        },
        {
            "type": "merchant_payment",
            "amount_minor": j.merchant_minor,
            "status": "confirmed" if paid_m else "pending",
            "assignment_version": version,
        },
        {
            "type": "driver_payment",
            "driver_id": driver_id,
            "amount_minor": j.driver_fee_minor,
            "status": "confirmed" if paid_d else "pending",
            "assignment_version": version,
        },
    )


def _assignment(names: tuple[str, ...]) -> tuple[str | None, str | None]:
    driver = route = None
    for n in names:
        if n.startswith("assign:"):
            _, driver, route = n.split(":")
    return driver, route


def make_plan(
    world: World, completed: tuple[str, ...] = (), prev: Plan | None = None
) -> Plan:
    acts = build_actions(world)
    start, kept = _restore_state(world, completed, acts)
    path, res = astar(start, acts)
    if path is None and "picked_up" not in start:
        # Before pickup, route/driver choices are revocable: retry with them released.
        irrev = tuple(n for n in completed if n not in acts or acts[n].irreversible)
        start2, kept2 = _restore_state(world, irrev, acts)
        path2, res2 = astar(start2, acts)
        if path2 is not None:
            start, kept, path, res = start2, kept2, path2, res2
    prev_pair = (prev.driver_id, prev.route_id) if prev else (None, None)
    if path is None:
        reasons = [
            v
            for r in world.routes
            for d in world.drivers
            for v in check_candidate(world, d, r)
        ]
        if res == "search_bound":
            reasons.append(
                {"clause": "SEARCH-BOUND", "detail": "expansion limit reached"}
            )
        driver, route = _assignment(kept)
        version = (
            (prev.assignment_version + 1) if prev and prev_pair != (None, None) else 0
        )
        return Plan(
            "refused",
            kept,
            (),
            0,
            None,
            None,
            version,
            (),
            tuple(reasons),
            resolution="escalate" if "pickup" in kept else "cancel",
            world_revision=world.revision,
        )
    names = kept + path
    driver, route = _assignment(names)
    version = prev.assignment_version if prev else 0
    if driver is not None and (
        version == 0 or (prev_pair != (None, None) and prev_pair != (driver, route))
    ):
        version += 1
    steps = tuple(Step(n, acts[n].cost, acts[n].gate) for n in path)
    done = set(kept)
    return Plan(
        "planned",
        kept,
        steps,
        sum(s.cost for s in steps),
        driver,
        route,
        version,
        _entitlements(world, driver, version, done),
        world_revision=world.revision,
    )


def replan(
    prev: Plan, world: World, completed: tuple[str, ...] | None = None
) -> tuple[Plan, PlanChange]:
    """Replan after inputs changed. Only pending steps are replaced; confirmed ones are preserved
    (revocable, non-money assignments are dropped only when the new inputs invalidate them)."""
    done = prev.completed if completed is None else completed
    new = make_plan(world, done, prev)
    causes: list[dict] = []
    if prev.driver_id and prev.route_id:
        d = next((x for x in world.drivers if x.id == prev.driver_id), None)
        r = next((x for x in world.routes if x.id == prev.route_id), None)
        if d is None or r is None:
            causes.append(
                {
                    "clause": "INPUT-1",
                    "detail": "assigned driver or route no longer offered",
                }
            )
        else:
            causes += check_candidate(world, d, r)
    old, newn = prev.step_names, new.step_names
    change = PlanChange(
        old_steps=old,
        new_steps=newn,
        removed=tuple(n for n in old if n not in newn),
        added=tuple(n for n in newn if n not in old),
        kept_completed=tuple(n for n in new.completed if n in prev.completed),
        cause_clauses=tuple(sorted({c["clause"] for c in causes})),
        causes=tuple(causes),
    )
    return new, change


def payment_commands(plan: Plan, replay: bool = False) -> list[dict]:
    """Money commands for pending payment steps. Replay/scrub never produces any."""
    if replay or plan.status != "planned":
        return []
    j = {e["type"]: e for e in plan.entitlements}
    out = []
    for s in plan.steps:
        if s.name in ("pay_merchant", "pay_driver"):
            e = j["merchant_payment" if s.name == "pay_merchant" else "driver_payment"]
            out.append(
                {
                    "op": s.name,
                    "amount_minor": e["amount_minor"],
                    "idempotency_key": f"{s.name}:v{plan.assignment_version}",
                }
            )
    return out
