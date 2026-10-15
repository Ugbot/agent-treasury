"""Four scripted scenarios that drive the real services through the agent + policy path.

Map positions, coverage records and receipts are SIMULATED inputs (labelled). Money moves only
through the gateway (mock by default, Stripe test mode when configured), only after the policy
check, and runs stop at human-approval points until an approval bound to the amount,
currency, recipient and effective policy hash is supplied.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from . import planner as pl
from .agent import (
    Agent,
    AgentContext,
    PlannerSession,
    PolicyVerdict,
    Proposal,
    client_from_env,
    party_for,
    plan_gate,
)
from .audit import AuditLog
from .fuel import FuelService
from .gateway import CapabilityUnavailable, GatewayError, LiveModeError, PaymentGateway
from .geo import Point
from .ledger import Ledger
from .policies import PolicyRegistry
from .policy import DEFAULT_POLICY, default_currency
from .proof import ReceiptNonces, approval_digest, seed_order, verify_proof
from .refunds import Order as RefundOrder
from .refunds import RefundService
from .stripe_gateway import audit_scope

SCENARIO_IDS = ("deliver_and_release", "fuel_entitlement", "coverage_replan", "cancel_refund")
TITLES = {
    "deliver_and_release": "Delivery proven, merchant and driver paid (merchant leg needs approval)",
    "fuel_entitlement": "Capped fuel allowance for the assigned driver, revoked when coverage lapses",
    "coverage_replan": "Coverage changes before pickup: replan to another eligible driver and pay them; escalate only if none",
    "cancel_refund": "Cancel before pickup refunds; cancel after pickup escalates",
}
LABEL = "simulated"
MAX_AGENT_STEPS = 6

N = pl.Zone("N", (Point(51.500, -0.12), Point(51.500, -0.08), Point(51.510, -0.08), Point(51.510, -0.12)),
            "permitted")
ROUTE_A = pl.Route("R1", (Point(51.502, -0.11), Point(51.508, -0.09)))
DEST = Point(51.508, -0.09)


class ScenarioError(Exception):
    pass


@dataclass
class Runtime:
    ledger: Ledger
    gateway: PaymentGateway
    registry: PolicyRegistry
    audit: AuditLog
    fuel: FuelService
    refunds: RefundService
    llm_client: object | None = None
    use_llm: bool = True

    @property
    def mode(self) -> str:
        return self.gateway.mode


@dataclass
class Run:
    run_id: str
    scenario: str
    mission_id: str
    stage: int = 0
    status: str = "running"  # running | awaiting_approval | completed | unavailable | escalated
    log: list[dict] = field(default_factory=list)
    money: list[dict] = field(default_factory=list)
    pending: dict[str, dict] = field(default_factory=dict)    # kind -> approval request incl. digest
    approved: dict[str, str] = field(default_factory=dict)    # kind -> digest the human approved
    state: dict = field(default_factory=dict)
    unavailable: str = ""

    def view(self, rt: Runtime) -> dict:
        return {"missionId": self.mission_id, "run_id": self.run_id, "scenario": self.scenario, "mission_id": self.mission_id,
                "label": LABEL, "inputs_note": "map, coverage and receipt inputs are simulated",
                "mode": rt.mode, "agent": {"agent_mode": "llm+goap" if rt.llm_client and rt.use_llm else "goap_only"},
                "status": self.status, "stage": self.stage, "steps": self.log, "money": self.money,
                "approvals_needed": [{"leg": k.removeprefix("pay_"), **{x: v[x] for x in
                                     ("kind", "amount_minor", "currency", "policy_hash", "recipient")}}
                                     for k, v in self.pending.items() if k not in self.approved],
                "unavailable": self.unavailable or None,
                "refund_id": self.state.get("refund_id"), "phases": self.state.get("phases", [])}


# ----------------------------------------------------------------------------- helpers

def build_runtime(ledger: Ledger, gateway: PaymentGateway, registry: PolicyRegistry, *,
                  fuel: FuelService | None = None, refunds: RefundService | None = None,
                  llm_client: object | None = None, from_env: bool = False) -> Runtime:
    return Runtime(ledger, gateway, registry, AuditLog(ledger.db), fuel or FuelService(gateway),
                   refunds or RefundService(gateway), client_from_env() if from_env else llm_client)


def _event(rt: Runtime, mid: str, key: str, patch: dict, actor: str = "system", summary: str = "",
           party: str = "sender") -> int:
    """Apply an input event. Ledger.apply_event writes the one input.event audit entry (same
    transaction), so nothing is appended here; call this BEFORE the replan it causes."""
    rev = rt.ledger.mission(mid)["revision"]
    return rt.ledger.apply_event(f"{mid}:{key}", mid, rev, patch, actor=actor, party=party,
                                 summary=summary or f"{key}: {patch}", audit_extra={"label": LABEL})


def _ctx(rt: Runtime, run: Run, session: PlannerSession, **facts) -> AgentContext:
    import json
    m = rt.ledger.mission(run.mission_id)
    return AgentContext(run.mission_id, m["currency"].lower(), json.loads(m["inputs"]), m["revision"], session,
                        {**run.state.get("facts", {}), **facts},
                        {k: {"amount_minor": v["amount_minor"]} for k, v in run.pending.items()
                         if k not in run.approved})


def _digest(run: Run, p: Proposal, v: PolicyVerdict) -> str:
    dest = run.state["destinations"].get(p.kind, "")
    return approval_digest(f"{run.mission_id}:{p.kind}", p.amount_minor or 0,
                           f"{dest}|{(p.currency or '').lower()}", v.effective_hash)  # type: ignore[arg-type]


def _agent(rt: Runtime, run: Run, executor: Callable) -> Agent:
    def approval(p: Proposal, ctx: AgentContext, v: PolicyVerdict) -> bool:
        return run.approved.get(p.kind) == _digest(run, p, v)

    return Agent(rt.audit, rt.registry, client=rt.llm_client, executor=executor, approval_check=approval,
                 mode=rt.mode)


def _drive(rt: Runtime, run: Run, agent: Agent, ctx_fn: Callable[[], AgentContext], *,
           max_steps=MAX_AGENT_STEPS) -> str:
    """Run agent steps until nothing is left, something is refused, or a step cannot execute."""
    for _ in range(max_steps):
        res = agent.step(ctx_fn(), use_llm=rt.use_llm)
        run.log.append(res.to_dict())
        if res.approval_request:
            req = res.approval_request
            dest = run.state["destinations"].get(req["kind"], "")
            p = Proposal(**{k: req[k] for k in ("kind", "amount_minor", "currency")})
            v = PolicyVerdict("hold", {}, effective_hash=req["policy_hash"])
            digest = _digest(run, p, v)
            if run.approved.get(req["kind"]) != digest:
                run.approved.pop(req["kind"], None)  # a changed amount/policy voids a prior approval
            fresh = req["kind"] not in run.pending or run.pending[req["kind"]]["digest"] != digest
            run.pending[req["kind"]] = {**req, "recipient": dest, "digest": digest}
            if fresh:
                continue  # other legs may still proceed; this one now waits for a human
            return "stopped"
        if res.status == "none":
            return "done"
        if res.status != "approved" or (res.execution or {}).get("state") in ("unavailable", "failed", "not_executable"):
            return "stopped"
    return "done"


def _transfer_executor(rt: Runtime, run: Run, session: PlannerSession):
    def ex(p: Proposal, ctx: AgentContext, v: PolicyVerdict) -> dict:
        leg = p.kind.removeprefix("pay_")
        if p.kind not in run.state["destinations"]:  # e.g. the model proposed request_approval: nothing to pay here
            return {"state": "not_executable", "message": f"{p.kind} is not a payment this scenario can execute"}
        dest = run.state["destinations"][p.kind]
        op = f"settle:{run.state['order_id']}:{leg}"
        return _money(rt, run, p, op, dest, v, lambda: rt.gateway.transfer(op, p.amount_minor, ctx.currency, dest),
                      lambda: session.complete(p.kind))
    return ex


def _money(rt: Runtime, run: Run, p: Proposal, op: str, dest: str, v: PolicyVerdict, call, after) -> dict:
    party = party_for(p.kind)
    try:
        with audit_scope(run.mission_id, party):
            r = call()
    except LiveModeError:
        raise
    except CapabilityUnavailable as e:
        out = {"state": "unavailable", "operation_id": op, "message": f"{e}. Setup: {e.setup}"}
        rt.audit.append("stripe", "stripe.call", f"{p.kind} unavailable: {e.reason}", mission_id=run.mission_id,
                        party=party, payload={"op": op, **out}, policy=v.policy, mode=rt.mode)
        run.unavailable = out["message"]
        return out
    except GatewayError as e:
        out = {"state": "failed", "operation_id": op, "message": f"{type(e).__name__}: {e}"}
        rt.audit.append("stripe", "stripe.call", f"{p.kind} failed (outcome unknown, reconcile before retry)",
                        mission_id=run.mission_id, party=party, payload={"op": op, **out}, policy=v.policy, mode=rt.mode)
        return out
    if r.livemode:
        raise LiveModeError("livemode object rejected")
    m = rt.ledger.mission(run.mission_id)
    rt.ledger.pay_once(op, run.mission_id, p.amount_minor or r.amount_minor, m["currency"], r.mode, r.provider_ref)
    after()
    out = {"state": "confirmed", "operation_id": op, "provider_ref": r.provider_ref, "mode": r.mode,
           "livemode": r.livemode, "amount_minor": r.amount_minor, "currency": r.currency, "label": rt.mode}
    run.money.append({"kind": p.kind, **out, "destination": dest})
    rt.audit.append("stripe", "stripe.call", f"{p.kind} {r.amount_minor} {r.currency} -> {r.provider_ref}",
                    mission_id=run.mission_id, party=party,
                    payload={"op": op, "destination": dest, "status": r.status},
                    policy=v.policy, stripe={"ids": [r.provider_ref], "livemode": r.livemode, "op": op},
                    mode=rt.mode)
    return out


def _collect(rt: Runtime, run: Run, amount: int) -> str | None:
    """Money in: the customer payment the transfers/refund draw on. Audited as a Stripe call."""
    m = rt.ledger.mission(run.mission_id)
    op = f"pay:{run.mission_id}"
    try:
        with audit_scope(run.mission_id, "sender"):
            r = rt.gateway.create_payment(op, amount, m["currency"].lower())
    except CapabilityUnavailable as e:
        run.status, run.unavailable = "unavailable", f"{e}. Setup: {e.setup}"
        rt.audit.append("stripe", "stripe.call", f"collection unavailable: {e.reason}", mission_id=run.mission_id,
                        party="sender", payload={"op": op, "setup": e.setup}, mode=rt.mode)
        return None
    if r.livemode:
        raise LiveModeError("livemode object rejected")
    rt.ledger.pay_once(op, run.mission_id, amount, m["currency"], r.mode, r.provider_ref)
    run.money.append({"kind": "collect", "state": "confirmed", "operation_id": op, "provider_ref": r.provider_ref,
                      "mode": r.mode, "livemode": r.livemode, "amount_minor": amount, "currency": r.currency})
    rt.audit.append("stripe", "stripe.call", f"collected {amount} {r.currency} -> {r.provider_ref}",
                    mission_id=run.mission_id, party="sender", payload={"op": op},
                    stripe={"ids": [r.provider_ref], "livemode": r.livemode, "op": op}, mode=rt.mode)
    return r.provider_ref


def _destinations(rt: Runtime) -> dict | None:
    a, b = os.environ.get("STRIPE_CONNECT_ACCOUNT_A"), os.environ.get("STRIPE_CONNECT_ACCOUNT_B")
    if rt.mode == "stripe_test":
        return {"pay_merchant": a, "pay_driver": b} if a and b else None
    return {"pay_merchant": a or "acct_merchant_test", "pay_driver": b or "acct_driver_test"}


def _world(job: pl.Job, drivers, coverage, routes=(ROUTE_A,)) -> pl.World:
    return pl.World(job, tuple(drivers), tuple(coverage), (N,), tuple(routes))


def _cov(driver: str, valid_until: float = 1e9, value: int = 50_000) -> pl.Coverage:
    return pl.Coverage(driver, ("parcel",), ("van",), value, ("N",), valid_until)


def _new_mission(rt: Runtime, run: Run, amount: int, patch: dict | None = None) -> None:
    cur = default_currency(DEFAULT_POLICY) or "usd"
    rt.ledger.create_mission(run.mission_id, amount, cur)
    _event(rt, run.mission_id, "scenario", {"scenario": run.scenario, "label": LABEL, **(patch or {})},
           summary=f"scenario {run.scenario} started (inputs {LABEL})")


def _travel_clean(session: PlannerSession) -> bool:
    r = next((x for x in session.world.routes if x.id == session.plan.route_id), None)
    return r is not None and not pl.travel_violations(session.world, r)


# ----------------------------------------------------------------------------- scenario 1

def _s1_setup(rt: Runtime, run: Run) -> bool:
    dests = _destinations(rt)
    if dests is None:
        run.status = "unavailable"
        run.unavailable = ("Stripe test mode needs connected accounts. Setup: run the sandbox bootstrap and set "
                           "STRIPE_CONNECT_ACCOUNT_A and STRIPE_CONNECT_ACCOUNT_B in .env.")
        return False
    cur = default_currency(DEFAULT_POLICY) or "usd"
    order = seed_order(f"ord-{run.mission_id}", destination=DEST, currency=cur,
                       merchant_destination=dests["pay_merchant"], driver_destination=dests["pay_driver"])
    job = pl.Job(value_minor=order.total_minor, start_ts=0, expected_duration_s=3600,
                 merchant_minor=order.merchant_minor, driver_fee_minor=order.driver_minor)
    world = _world(job, [pl.Driver("drv_1")], [_cov("drv_1")])
    _new_mission(rt, run, order.total_minor, {"insured": True, "location": "depot"})
    session = PlannerSession(rt.audit, run.mission_id, world, rt.mode)
    _event(rt, run.mission_id, "route-check", {"prohibited_zone": not _travel_clean(session)},
           summary="route checked against zones by the planner (server-owned fact)")
    order = seed_order(order.id, destination=DEST, currency=cur, assignment_version=session.plan.assignment_version,
                       merchant_destination=dests["pay_merchant"], driver_destination=dests["pay_driver"],
                       assigned_driver=session.plan.driver_id or "drv_1")
    run.state.update(order=order, order_id=order.id, session=session, destinations=dests,
                     nonces=ReceiptNonces())
    return True


def _s1_collect(rt, run):
    return _collect(rt, run, run.state["order"].total_minor) is not None


def _s1_early(rt, run):
    s: PlannerSession = run.state["session"]
    ag = _agent(rt, run, _transfer_executor(rt, run, s))
    res = ag.step(_ctx(rt, run, s), use_llm=False)  # deliberate premature release attempt
    run.log.append({**res.to_dict(), "scripted": "premature release attempt before delivery proof"})
    return True


def _s1_delivery(rt, run):
    s: PlannerSession = run.state["session"]
    o = run.state["order"]
    nonces: ReceiptNonces = run.state["nonces"]
    s.complete("select_route:R1", f"assign:{o.assigned_driver}:R1", "pickup", "deliver")
    now = 1000.0
    token = nonces.issue(o, now)
    kw = {"driver_id": o.assigned_driver, "position": Point(51.5080, -0.0901), "position_ts": now - 5,
          "recipient_id": o.recipient_id, "nonce": token, "assignment_version": o.assignment_version, "now": now}
    bad = verify_proof(o, nonces, **{**kw, "position": Point(51.52, -0.05)}, consume=False)
    rt.audit.append("system", "delivery.proof", "proof attempt from outside the destination radius: rejected",
                    mission_id=run.mission_id, party="driver",
                    payload={"gates": bad.gates, "reasons": bad.reasons, "label": LABEL}, mode=rt.mode)
    proof = verify_proof(o, nonces, **kw)
    rt.audit.append("system", "delivery.proof", f"proof {'accepted' if proof.ok else 'rejected'} (simulated position)",
                    mission_id=run.mission_id, party="receiver",
                    payload={"gates": proof.gates, "reasons": proof.reasons, "label": LABEL}, mode=rt.mode)
    replay = verify_proof(o, nonces, **kw, consume=False)
    rt.audit.append("system", "delivery.proof", "nonce replay rejected", mission_id=run.mission_id, party="driver",
                    payload={"gates": replay.gates, "reasons": replay.reasons}, mode=rt.mode)
    if not proof.ok:
        run.status = "escalated"
        return False
    s.complete("confirm_receipt")
    _event(rt, run.mission_id, "receipt", {"receipt_confirmed": True}, "receiver",
           "receipt confirmed from verified proof (server-owned fact)", party="receiver")
    return True


def _s1_release(rt, run):
    s: PlannerSession = run.state["session"]
    ag = _agent(rt, run, _transfer_executor(rt, run, s))
    _drive(rt, run, ag, lambda: _ctx(rt, run, s))
    paid = {m["kind"] for m in run.money}
    if {"pay_merchant", "pay_driver"} <= paid:
        run.status = "completed"
        return True
    waiting = [k for k in run.pending if k not in run.approved]
    run.status = "awaiting_approval" if waiting else ("unavailable" if run.unavailable else "stopped")
    return False


# ----------------------------------------------------------------------------- scenario 2

def _s2_setup(rt, run):
    job = pl.Job(value_minor=3000, start_ts=0, expected_duration_s=3600, merchant_minor=2500, driver_fee_minor=700,
                 fuel_cap_minor=800)
    world = _world(job, [pl.Driver("drv_1")], [_cov("drv_1")])
    _new_mission(rt, run, 3200, {"insured": True, "location": "depot"})
    s = PlannerSession(rt.audit, run.mission_id, world, rt.mode)
    s.complete("select_route:R1", f"assign:{s.plan.driver_id}:R1")
    run.state.update(session=s, facts={"focus": "fuel"}, destinations={})
    f = rt.fuel
    f.stops["stop_1"] = Point(51.505, -0.10)
    f.update_job(run.mission_id, status="active", driver_id=s.plan.driver_id,
                 assignment_version=s.plan.assignment_version, coverage_valid=True, route=ROUTE_A.points,
                 position=Point(51.505, -0.1), position_ts=f.clock())
    if rt.mode == "mock" and f.issuing_balance_minor is None:
        f.set_issuing_balance(5000)  # simulated Issuing balance, mock mode only
    return True


def _s2_issue(rt, run):
    s: PlannerSession = run.state["session"]

    def ex(p, ctx, v):
        try:
            e = rt.fuel.issue(run.mission_id, p.driver_id, s.plan.assignment_version)
        except (ValueError, CapabilityUnavailable, GatewayError) as err:
            msg = f"{err}" + (f" Setup: {err.setup}" if isinstance(err, CapabilityUnavailable) else "")
            run.unavailable = msg if isinstance(err, CapabilityUnavailable) else run.unavailable
            rt.audit.append("system", "fuel.issue", f"fuel allowance not issued: {msg}", mission_id=run.mission_id,
                            party="driver", payload={"unavailable": msg}, policy=v.policy, mode=rt.mode)
            return {"state": "unavailable" if isinstance(err, CapabilityUnavailable) else "failed", "message": msg}
        s.effects.add("fuel_issued")
        run.state["entitlement"] = e.entitlement_id
        rt.audit.append("system", "fuel.issue", f"fuel entitlement {e.entitlement_id} cap {e.cap_minor}",
                        mission_id=run.mission_id, party="driver", policy=v.policy,
                        stripe={"ids": [e.card_ref], "livemode": False, "op": "issue_card"}, mode=rt.mode)
        return {"state": "confirmed", "entitlement_id": e.entitlement_id, "card_ref": e.card_ref,
                "cap_minor": e.cap_minor}

    ag = _agent(rt, run, ex)
    _drive(rt, run, ag, lambda: _ctx(rt, run, s), max_steps=2)
    if "fuel_issued" not in s.effects:
        run.status = "unavailable" if run.unavailable else "escalated"
        return False
    return True


def _s2_spend(rt, run):
    eid, f = run.state["entitlement"], rt.fuel
    cases = [("on-route stop, within cap", 600, "5541", "stop_1"),
             ("over the remaining cap", 600, "5541", "stop_1"),
             ("wrong merchant category", 100, "5812", "stop_1")]
    for label, amt, cat, stop in cases:
        try:
            a = f.attempt(eid, amt, cat, "GB", stop)
            cap = f.capture(a.authorization_id) if a.approved and a.state == "reserved" else None
        except (CapabilityUnavailable, GatewayError) as err:
            run.unavailable = f"{err}"
            rt.audit.append("system", "fuel.auth", f"{label}: provider unavailable", mission_id=run.mission_id,
                            party="driver", payload={"unavailable": f"{err}"}, mode=rt.mode)
            run.status = "unavailable"
            return False
        out = {"case": label, "approved": a.approved, "reasons": list(a.reasons), "state": a.state,
               "captured_minor": cap.captured_minor if cap else 0, "label": f"{rt.mode} (simulated merchant)"}
        run.log.append({"status": "card_attempt", **out})
        rt.audit.append("system", "fuel.auth", f"{label}: {'captured' if cap else 'declined ' + ','.join(a.reasons)}",
                        mission_id=run.mission_id, party="driver", payload=out,
                        stripe={"ids": [x for x in (a.provider_ref, a.issuing_transaction) if x], "livemode": False,
                                "op": "fuel_authorization"} if a.provider_ref else None, mode=rt.mode)
        if cap:
            run.money.append({"kind": "fuel_capture", "state": "confirmed", "provider_ref": cap.issuing_transaction,
                              "amount_minor": cap.captured_minor, "mode": rt.mode, "livemode": False})
    return True


def _s2_coverage_lapse(rt, run):
    s: PlannerSession = run.state["session"]
    _event(rt, run.mission_id, "coverage", {"insured": False}, summary="coverage lapsed (simulated)",
           party="driver")  # the cause is recorded before the replan it triggers
    s.apply("coverage_changed", {"coverage": _cov("drv_1", valid_until=10.0)},
            "coverage for drv_1 lapsed (simulated insurer record): fuel authority must close")
    rt.fuel.update_job(run.mission_id, coverage_valid=False)
    a = rt.fuel.attempt(run.state["entitlement"], 100, "5541", "GB", "stop_1")
    rt.audit.append("system", "fuel.auth", f"after lapse: declined {','.join(a.reasons)}", mission_id=run.mission_id,
                    party="driver", payload={"approved": a.approved, "reasons": list(a.reasons)}, mode=rt.mode)
    run.log.append({"status": "card_attempt", "case": "after coverage lapse", "approved": a.approved,
                    "reasons": list(a.reasons)})
    run.status = "completed"
    return True


# ----------------------------------------------------------------------------- scenario 3

def _s3_setup(rt, run):
    """Two simulated drivers with different coverage records: A (nearer, narrow cover) and B (further,
    wider cover). All coverage, zones and positions here are SIMULATED inputs."""
    dests = _destinations(rt)
    if dests is None:
        run.status = "unavailable"
        run.unavailable = ("Stripe test mode needs connected accounts. Setup: run the sandbox bootstrap and set "
                           "STRIPE_CONNECT_ACCOUNT_A and STRIPE_CONNECT_ACCOUNT_B in .env.")
        return False
    job = pl.Job(value_minor=3000, start_ts=0, expected_duration_s=3600, merchant_minor=2500, driver_fee_minor=700,
                 fuel_cap_minor=800)
    cov_b = pl.Coverage("drv_2", ("parcel", "fragile"), ("van", "bike"), 80_000, ("N",), 1e9)  # simulated, wider than A
    world = _world(job, [pl.Driver("drv_1"), pl.Driver("drv_2", eta_s=300)], [_cov("drv_1"), cov_b])
    _new_mission(rt, run, 3200, {"insured": True, "location": "depot",
                                 "drivers": {"drv_1": "simulated coverage: parcel/van, max 50000, zone N",
                                             "drv_2": "simulated coverage: parcel+fragile/van+bike, max 80000, zone N"}})
    s = PlannerSession(rt.audit, run.mission_id, world, rt.mode)
    if _collect(rt, run, 3200) is None:
        return False
    run.state.update(session=s, destinations=dests, order_id=run.mission_id, facts={})
    s.complete(*s.plan.step_names[:2])  # route + assignment (driver A) confirmed before pickup
    f = rt.fuel
    f.stops["stop_1"] = Point(51.505, -0.10)
    f.update_job(run.mission_id, status="active", driver_id=s.plan.driver_id,
                 assignment_version=s.plan.assignment_version, coverage_valid=True, route=ROUTE_A.points,
                 position=Point(51.505, -0.1), position_ts=f.clock())
    if rt.mode == "mock" and f.issuing_balance_minor is None:
        f.set_issuing_balance(5000)  # simulated Issuing balance, mock mode only
    try:
        e = f.issue(run.mission_id, s.plan.driver_id, s.plan.assignment_version)
        run.state["entitlement"] = e.entitlement_id
        rt.audit.append("system", "fuel.issue", f"fuel entitlement {e.entitlement_id} cap {e.cap_minor} for driver "
                        f"{s.plan.driver_id}", mission_id=run.mission_id, party="driver",
                        payload={"entitlement_id": e.entitlement_id, "driver_id": s.plan.driver_id},
                        stripe={"ids": [e.card_ref], "livemode": False, "op": "issue_card"}, mode=rt.mode)
    except (ValueError, CapabilityUnavailable, GatewayError) as err:
        rt.audit.append("system", "fuel.issue", f"fuel allowance not issued: {err}", mission_id=run.mission_id,
                        party="driver", payload={"unavailable": f"{err}"}, mode=rt.mode)
    return True


def _revoke_fuel(rt, run, driver: str, reason: str) -> None:
    rt.fuel.update_job(run.mission_id, coverage_valid=False)
    for e in rt.fuel.entitlements.values():
        if e.job_id == run.mission_id and e.driver_id == driver and e.state == "revoked":
            if run.state.setdefault("revoked_logged", set()) >= {e.entitlement_id}:
                continue
            run.state["revoked_logged"].add(e.entitlement_id)
            rt.audit.append("system", "fuel.revoke", f"unused fuel authority {e.entitlement_id} for driver {driver} "
                            f"revoked: {e.revoke_reason} ({reason})", mission_id=run.mission_id, party="driver",
                            payload={"driver_id": driver, "entitlement_id": e.entitlement_id, "reason": e.revoke_reason,
                                     "label": LABEL}, mode=rt.mode)


def _s3_lapse_a(rt, run):
    s: PlannerSession = run.state["session"]
    old = s.plan
    old_driver = old.driver_id
    cov_a = _cov("drv_1", valid_until=10.0)
    _event(rt, run.mission_id, "cover-a", {"coverage_changed": "A"}, summary="coverage A changed (simulated)",
           party="driver")  # cause first, then the effects it triggers
    preview = pl.apply_event(s.world, pl.WorldEvent("coverage_changed", {"coverage": cov_a}))
    why = pl.check_candidate(preview, next(d for d in preview.drivers if d.id == old_driver),
                             next(r for r in preview.routes if r.id == old.route_id))
    rt.audit.append("planner", "plan.refused", f"dispatch to driver {old_driver} refused: coverage no longer valid "
                    f"through completion", mission_id=run.mission_id, party="driver",
                    payload={"reasons": [dict(x) for x in why], "driver_id": old_driver,
                             "policy_weakened": False, "simulated": True}, mode=rt.mode)
    _revoke_fuel(rt, run, old_driver, "assigned driver no longer covered")
    s.apply("coverage_changed", {"coverage": cov_a},
            "driver A cover expired (simulated insurer record): replan pending work only")
    new = s.plan
    if new.status == "planned" and new.driver_id != old_driver:
        rt.fuel.update_job(run.mission_id, driver_id=new.driver_id, assignment_version=new.assignment_version,
                           coverage_valid=True)
        rt.audit.append("planner", "assignment.change",
                        f"assignment changed {old_driver} -> {new.driver_id} (v{old.assignment_version} -> "
                        f"v{new.assignment_version}); allowance recomputed", mission_id=run.mission_id,
                        party="driver",
                        payload={"old_driver": old_driver, "new_driver": new.driver_id,
                                 "old_version": old.assignment_version, "new_version": new.assignment_version,
                                 "old_fuel": next((e for e in old.entitlements if e["type"] == "fuel"), None),
                                 "new_fuel": s.entitlement("fuel"), "simulated": True}, mode=rt.mode)
    ag = _agent(rt, run, lambda p, c, v: {"state": "noop"})
    # The old plan's agent proposal for the former driver must be blocked by the plan gate.
    stale = Proposal(kind="pay_driver", amount_minor=old.entitlements[2]["amount_minor"], driver_id=old_driver,
                     currency=rt.ledger.mission(run.mission_id)["currency"].lower(),
                     reason="stale-plan probe (scripted): pay the previously assigned driver")
    ctx = _ctx(rt, run, s)
    ok, why = plan_gate(stale, ctx)
    rt.audit.append("agent", "agent.proposal", "stale-plan probe proposes pay_driver for driver A",
                    mission_id=run.mission_id, party="driver", payload={"proposal": stale.to_dict(), "scripted": True}, mode=rt.mode)
    rt.audit.append("planner", "agent.verdict", f"pay_driver for A: plan_gate={'pass' if ok else 'fail'} -> blocked",
                    mission_id=run.mission_id, party="driver", payload={"plan_gate": {"ok": ok, "reason": why}, "executed": False},
                    policy={"id": "plan-gate", "verdict": "deny" if not ok else "allow", "clauses": []}, mode=rt.mode)
    run.log.append({"status": "blocked_by_plan" if not ok else "approved", "proposal": stale.to_dict(),
                    "source": "scripted", "plan_gate": {"ok": ok, "reason": why}, "executed": False})
    res = ag.step(_ctx(rt, run, s), use_llm=False)  # real next step on the new plan (held: no receipt yet)
    run.log.append(res.to_dict())
    run.state["new_driver"] = s.plan.driver_id
    return True


def _s3_deliver_pay(rt, run):
    """Driver B completes the job (simulated receipt); the agent proposes, policy allows, B is paid once."""
    s: PlannerSession = run.state["session"]
    if s.plan.status != "planned":
        run.status = "escalated"
        return False
    upto = s.plan.step_names.index("confirm_receipt") + 1
    s.complete(*s.plan.step_names[:upto])
    rt.fuel.update_job(run.mission_id, status="completed")  # unused authority closes on completion
    _event(rt, run.mission_id, "receipt", {"receipt_confirmed": True}, "receiver",
           f"receipt confirmed for driver {s.plan.driver_id} (simulated)", party="receiver")
    _drive(rt, run, _agent(rt, run, _transfer_executor(rt, run, s)), lambda: _ctx(rt, run, s))
    paid = {m["kind"] for m in run.money}
    if {"pay_merchant", "pay_driver"} <= paid:
        run.status = "completed"
        return True
    waiting = [k for k in run.pending if k not in run.approved]
    run.status = "awaiting_approval" if waiting else ("unavailable" if run.unavailable else "stopped")
    return False


def _s3_lapse_all(rt, run):
    s: PlannerSession = run.state["session"]
    _event(rt, run.mission_id, "cover-b", {"coverage_changed": "B"}, summary="coverage B changed (simulated)",
           party="driver")
    s.apply("coverage_changed", {"coverage": _cov("drv_2", valid_until=10.0)},
            "driver B cover expired too (simulated insurer record): no covered driver remains")

    def ex(p, c, v):
        return _escalate(rt, run, s, p, v)

    _drive(rt, run, _agent(rt, run, ex), lambda: _ctx(rt, run, s), max_steps=2)
    run.status = "escalated" if "escalated" in s.effects else "completed"
    return True


# ----------------------------------------------------------------------------- scenario 4

def _s4_setup(rt, run):
    job = pl.Job(value_minor=3000, merchant_minor=3000, driver_fee_minor=1000)
    world = _world(job, [pl.Driver("drv_1")], [_cov("drv_1")])
    _new_mission(rt, run, 4500, {"insured": True, "location": "depot"})
    s = PlannerSession(rt.audit, run.mission_id, world, rt.mode)
    ref = _collect(rt, run, 4500)
    if ref is None:
        return False
    cur = rt.ledger.mission(run.mission_id)["currency"].lower()
    run.state.update(session=s, destinations={}, payment_ref=ref, currency=cur)
    rt.refunds.register_order(RefundOrder(f"ord-{run.mission_id}", ref, 4500, cur, status="funded"))
    return True


def _escalate(rt: Runtime, run: Run, session: PlannerSession, p: Proposal, v: PolicyVerdict) -> dict:
    rt.ledger.db.execute("UPDATE missions SET status='escalated' WHERE id=?", (run.mission_id,))
    rt.ledger.db.commit()
    session.effects.add("escalated")
    rt.audit.append("system", "escalation", "mission escalated to a human: " + p.reason,
                    mission_id=run.mission_id, party="sender", policy=v.policy, mode=rt.mode)
    return {"state": "escalated"}


def _refund_executor(rt, run, oid, session):
    def ex(p, ctx, v):
        if p.kind == "escalate":
            return _escalate(rt, run, session, p, v)
        res = rt.refunds.request_refund(oid, f"{run.mission_id}:cancel:{oid}", {"label": LABEL})
        run.state.setdefault("facts", {})["refund_state"] = res["state"]
        rt.audit.append("system", "refund", f"refund {res['state']}: {res['decision']['reason']}",
                        mission_id=run.mission_id, party="sender", payload={"decision": res["decision"]},
                        policy=v.policy, stripe={"ids": [(res.get("provider") or {}).get("provider_ref", "")],
                                                 "livemode": False, "op": "refund"} if res.get("provider") else None,
                        mode=rt.mode)
        if res["state"] in ("confirmed", "succeeded") and res.get("provider"):
            run.money.append({"kind": "refund", "state": "confirmed", "provider_ref": res["provider"]["provider_ref"],
                              "amount_minor": res["amount_minor"], "mode": res["provider"]["mode"],
                              "livemode": res["provider"]["livemode"], "operation_id": res["operation_id"]})
        return {"state": res["state"], "reason": res["decision"]["reason"]}
    return ex


def _tag_phase(run: Run, start: int, phase: str) -> None:
    for st in run.log[start:]:
        st["phase"] = phase


def _phase_outcome(run: Run, phase: str, outcome: str, **extra) -> None:
    run.state.setdefault("phases", []).append({"phase": phase, "outcome": outcome, **extra})


def _s4_cancel_early(rt, run):
    """Phase 1 (primary): cancel before pickup, one full refund of the original payment (REFUND-1)."""
    s: PlannerSession = run.state["session"]
    oid = f"ord-{run.mission_id}"
    rt.refunds.register_order(RefundOrder(oid, run.state["payment_ref"], 4500, run.state["currency"],
                                          status="canceled"))
    _event(rt, run.mission_id, "cancel", {"cancelled": True}, "sender", "sender cancelled before pickup", party="sender")
    run.state.setdefault("facts", {})["cancelled"] = True
    start = len(run.log)
    _drive(rt, run, _agent(rt, run, _refund_executor(rt, run, oid, s)), lambda: _ctx(rt, run, s), max_steps=1)
    refunds = [m for m in run.money if m["kind"] == "refund"]
    if not refunds:
        run.status = "stopped"
        return False
    first = refunds[0]
    # A repeated cancel notification (webhook retry) must be an explicit no-op, never a second refund.
    again = rt.refunds.request_refund(oid, f"{run.mission_id}:cancel:{oid}:retry", {"label": LABEL})
    dup = bool(again.get("duplicate")) and again.get("operation_id") == first["operation_id"]
    rt.audit.append("system", "refund",
                    "refund duplicate_ignored: repeated cancel for an already refunded order" if dup
                    else f"refund repeat NOT ignored: {again.get('state')}",
                    mission_id=run.mission_id, party="sender",
                    payload={"outcome": "duplicate_ignored" if dup else again.get("state"),
                             "operation_id": first["operation_id"], "refund": first["provider_ref"],
                             "amount_minor": first["amount_minor"], "label": LABEL}, mode=rt.mode)
    run.log.append({"status": "duplicate_ignored", "name": "refund", "proposal": {"kind": "refund"},
                    "source": "goap", "executed": False, "note": "repeat cancel: no second refund",
                    "execution": {"state": "duplicate_ignored", "operation_id": first["operation_id"]}})
    _tag_phase(run, start, "primary_cancel_before_pickup")
    run.state["refund_id"] = first["provider_ref"]
    _phase_outcome(run, "primary_cancel_before_pickup", "refunded" if dup else "duplicate_not_ignored",
                   refund_id=first["provider_ref"], amount_minor=first["amount_minor"])
    return dup


def _s4_cancel_late(rt, run):
    """Phase 2 (separate): cancel AFTER pickup is refused by policy and escalated to a person.
    The primary refund already succeeded, so this phase never changes the overall run status."""
    s: PlannerSession = run.state["session"]
    cur = run.state["currency"]
    m2 = Run(run.run_id + ":late", run.scenario, run.mission_id + "-late")
    m2.log, m2.money = run.log, run.money  # one combined view for the caller
    _new_mission(rt, m2, 4500, {"insured": True})
    ref = _collect(rt, m2, 4500)
    if ref is None:
        run.status = "unavailable"
        return False
    oid = f"ord-{m2.mission_id}"
    # Cancelled after pickup: the order stays picked_up, so REFUND-1 holds it.
    rt.refunds.register_order(RefundOrder(oid, ref, 4500, cur, status="picked_up"))
    _event(rt, m2.mission_id, "cancel", {"cancelled": True}, "sender", "sender cancelled AFTER pickup", party="sender")
    s2 = PlannerSession(rt.audit, m2.mission_id, s.world, rt.mode)
    m2.state.update(facts={"cancelled": True}, destinations={})
    start = len(run.log)
    _drive(rt, m2, _agent(rt, m2, _refund_executor(rt, m2, oid, s2)), lambda: _ctx(rt, m2, s2), max_steps=3)
    _tag_phase(run, start, "separate_cancel_after_pickup")
    out = "refused_escalated" if "escalated" in s2.effects else "not_escalated"
    _phase_outcome(run, "separate_cancel_after_pickup", out, mission_id=m2.mission_id)
    rt.audit.append("system", "refund", f"after-pickup cancel phase outcome: {out}",
                    mission_id=m2.mission_id, party="sender",
                    payload={"phase": "separate_cancel_after_pickup", "outcome": out, "label": LABEL}, mode=rt.mode)
    return True


# ----------------------------------------------------------------------------- runner

def step_once(rt: Runtime, run: Run) -> dict | None:
    """One agent step for a run (POST /api/agent/step). Only runs with a payment executor can step."""
    s = run.state.get("session")
    if s is None or run.scenario != "deliver_and_release" or run.stage < 4:
        raise ScenarioError("stepwise agent steps are available for deliver_and_release after delivery proof")
    n = len(run.log)
    _drive(rt, run, _agent(rt, run, _transfer_executor(rt, run, s)), lambda: _ctx(rt, run, s), max_steps=1)
    paid = {m["kind"] for m in run.money}
    if {"pay_merchant", "pay_driver"} <= paid:
        run.status = "completed"
    elif any(k not in run.approved for k in run.pending):
        run.status = "awaiting_approval"
    return run.log[-1] if len(run.log) > n else None


STAGES: dict[str, list[Callable[[Runtime, Run], bool]]] = {
    "deliver_and_release": [_s1_setup, _s1_collect, _s1_early, _s1_delivery, _s1_release],
    "fuel_entitlement": [_s2_setup, _s2_issue, _s2_spend, _s2_coverage_lapse],
    "coverage_replan": [_s3_setup, _s3_lapse_a, _s3_deliver_pay],
    "coverage_replan_no_candidate": [_s3_setup, _s3_lapse_a, _s3_lapse_all],  # escalate-not-weaken path (tests)
    "cancel_refund": [_s4_setup, _s4_cancel_early, _s4_cancel_late],
}


class ScenarioRunner:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.runs: dict[str, Run] = {}
        self._lock = threading.RLock()  # concurrent retries of one run must not double-execute a stage

    def get(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    def run(self, scenario: str, run_id: str | None = None, approvals: list[str] | None = None) -> dict:
        with self._lock:
            return self._run(scenario, run_id, approvals)

    def _run(self, scenario: str, run_id: str | None = None, approvals: list[str] | None = None) -> dict:
        if scenario not in STAGES:
            raise ScenarioError(f"unknown scenario {scenario}")
        rid = run_id or f"{scenario}-{len(self.runs) + 1}"
        run = self.runs.get(rid)
        if run is None:
            run = self.runs[rid] = Run(rid, scenario, f"scn-{rid}")
            self.rt.audit.append("system", "scenario.start", f"scenario {scenario} started ({LABEL} inputs)",
                                 mission_id=run.mission_id, party="sender", payload={"run_id": rid, "label": LABEL}, mode=self.rt.mode)
        elif run.scenario != scenario:
            raise ScenarioError(f"run {rid} belongs to scenario {run.scenario}")
        if run.status in ("completed", "unavailable", "escalated") and not approvals:
            return run.view(self.rt)
        todo: list[tuple[str, dict]] = []
        for leg in approvals or []:  # validate every leg first: a refusal leaves no orphaned approval
            kind = leg if leg.startswith("pay_") else f"pay_{leg}"
            pend = run.pending.get(kind)
            if pend is None:
                self.rt.audit.append("approver", "approval.refused", f"approval refused: nothing pending for {leg}",
                                     mission_id=run.mission_id, party="approver",
                                     payload={"requested": list(approvals or []), "leg": leg, "applied": []},
                                     mode=self.rt.mode)
                raise ScenarioError(f"nothing pending approval for {leg}")
            todo.append((kind, pend))
        for kind, pend in todo:
            run.approved[kind] = pend["digest"]
            self.rt.audit.append("approver", "approval", f"human approved {kind} {pend['amount_minor']} "
                                 f"{pend['currency']} to {pend['recipient']}", mission_id=run.mission_id,
                                 party="approver",
                                 payload={"digest": pend["digest"], "bound": ["amount", "currency", "recipient",
                                                                              "policy_hash"]}, mode=self.rt.mode)
        run.status = "running"
        stages = STAGES[scenario]
        while run.stage < len(stages):
            with audit_scope(run.mission_id, "sender"):
                go = stages[run.stage](self.rt, run)
            if not go:
                if run.status == "running":
                    run.status = "stopped"
                break
            run.stage += 1
        else:
            if run.status == "running":
                run.status = "completed"
        self._audit_unavailable(run)
        return run.view(self.rt)

    def _audit_unavailable(self, run: Run) -> None:
        """An unavailable run records in the audit log what was unavailable and the setup fix."""
        if run.status != "unavailable" or run.state.get("unavailable_logged") == run.unavailable:
            return
        run.state["unavailable_logged"] = run.unavailable
        msg = run.unavailable or "a required capability is not available"
        what, _, setup = msg.partition("Setup:")
        self.rt.audit.append("system", "scenario.unavailable",
                             f"scenario {run.scenario} unavailable: {what.strip()[:200]}",
                             mission_id=run.mission_id, party="sender",
                             payload={"unavailable": msg, "setup": setup.strip(), "run_id": run.run_id,
                                      "stage": run.stage}, mode=self.rt.mode)
