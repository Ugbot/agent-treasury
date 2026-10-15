"""HTTP API. Backend owns policy and money authority; the browser only sends inputs."""
from __future__ import annotations

import json
import os
import threading

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import (
    bitemporal,
    routes_agent,
    routes_audit,
    routes_author,
    routes_chat,
    routes_delivery,
    routes_explain,
    routes_freeze,
    routes_fuel,
    routes_history,
    routes_invoices,
    routes_jobs,
    routes_pack,
    routes_policies,
    routes_refunds,
    workflows,
)
from .decision import DecisionEngine, org_currency
from .delivery_wiring import configure_services
from .ledger import Ledger
from .memory import CogneeMemory
from .policies import default_registry
from .policy import DEFAULT_POLICY, default_currency
from .routes_console import actor_from, build_console_router, error_response
from .wiring import SagaGatewayAdapter, gateway_from_env


class MissionIn(BaseModel):
    id: str
    amount_minor: int = Field(gt=0)
    currency: str | None = None  # default comes from the organisation policy (CURRENCY-1)
    recipient: str | None = None  # bound into any approval; defaults to recipient:<mission id>


class ReceiptIn(BaseModel):
    nonce: str | None = None
    recipient_id: str | None = None


class EventIn(BaseModel):
    """Client attestations only. Receipt, position and zone are server-owned: send `location`
    (a named place the server maps to coordinates) and `receipt` (proof request), never
    receipt_confirmed / prohibited_zone."""
    event_id: str
    expected_revision: int
    inputs: dict = {}
    location: str | None = None
    receipt: ReceiptIn | None = None


class ApprovalIn(BaseModel):
    event_id: str
    expected_revision: int
    amount_minor: int | None = None
    currency: str | None = None
    recipient: str | None = None
    policy_hash: str | None = None


class OverrideIn(BaseModel):
    event_id: str
    expected_revision: int
    clause_ids: list[str]
    reason: str
    policy_hash: str


_ORG_LOCKS: dict[str, threading.RLock] = {}
_ORG_LOCKS_GUARD = threading.Lock()


def _org_lock(org: str) -> threading.RLock:
    with _ORG_LOCKS_GUARD:
        return _ORG_LOCKS.setdefault(org, threading.RLock())


def create_app(ledger: Ledger | None = None, gateway=None, use_workflows: bool | None = None) -> FastAPI:
    """gateway: mock by default, Stripe test mode when STRIPE_SECRET_KEY is set.
    use_workflows: run payments through the DBOS workflow (default on when the app owns its ledger)."""
    led = ledger or Ledger(os.environ.get("TREASURY_DB", "treasury.sqlite"))
    gw = gateway or gateway_from_env()
    durable = (ledger is None) if use_workflows is None else use_workflows
    app = FastAPI(title="Agent Treasury")

    if durable:
        workflows.start_runtime(led, os.environ.get("TREASURY_DBOS_DB", "treasury_dbos.sqlite"),
                                SagaGatewayAdapter(gw))
        app.router.on_shutdown.append(workflows.stop_runtime)
    # One AuditLog (the ledger's), one policy registry per app, Cognee memory audited via the same log.
    app.state.ledger = led
    app.state.audit = led.audit
    registry = routes_policies._registry  # a registry installed beforehand (tests, embedding) wins
    if registry is None:
        registry = default_registry(CogneeMemory(audit=led.audit))
        routes_policies.set_registry(registry)
    elif hasattr(registry.memory, "attach_audit"):
        registry.memory.attach_audit(led.audit)
    routes_freeze.seed_freeze(registry)
    bitemporal.replay_registry(registry, led.audit)
    bitemporal.instrument_registry(registry, led.audit)
    configure_services(gw, db=led.db, audit=led.audit)
    routes_agent.configure(led, gw, registry=registry, fuel=routes_fuel.get_service(),
                           refunds=routes_refunds.get_service())

    def chat_context(mission_id: str):
        from .chat import ChatContext
        m = led.mission(mission_id)
        if m is None:
            return None
        decisions, evidence = [], []
        for row in led.db.execute("SELECT revision, body FROM decisions WHERE mission_id=? ORDER BY revision", (mission_id,)):
            b = json.loads(row["body"])
            for r in b["results"]:
                did = f"{mission_id}:{row['revision']}"
                eid = f"ev:{did}:{r['clause']}"
                decisions.append({"decisionId": did, "policyId": b["policy_id"], "version": b["policy_version"],
                                  "clauseId": r.get("clause") or "pause", "outcome": r["outcome"],
                                  "reason": json.dumps(r["evidence"]), "evidenceIds": [eid]})
                evidence.append({"evidenceId": eid, "text": json.dumps(r["evidence"])})
        return ChatContext(mission={"id": mission_id, "amount_minor": m["amount_minor"], "currency": m["currency"]},
                           decisions=decisions, evidence=evidence)

    routes_chat.set_context_provider(chat_context)

    engine = DecisionEngine(led, registry=lambda: registry, mode=lambda: gw.mode)
    if durable:
        workflows.runtime().decider = engine.decide  # workflow decide step uses the same registry-backed engine

    def decide(mission_id: str) -> dict:
        # Velocity is a count of prior payments: decide -> pay -> audit('payment') must be atomic per org,
        # and the decision is (re)made inside the lock so the count includes concurrent payments.
        with _org_lock("org"):
            return _decide_locked(mission_id)

    def _decide_locked(mission_id: str) -> dict:
        try:
            body = engine.decide(mission_id)
        except KeyError:
            raise HTTPException(404, "unknown mission") from None
        m = led.mission(mission_id)
        verdict = body["verdict"]
        if durable:
            # The workflow owns reservation, payment, reconciliation and compensation.
            workflows.start_mission(mission_id)
            workflows.deliver_event(mission_id, f"{mission_id}:{m['revision']}")
            if verdict == "allow":
                body["workflow"] = workflows.start_mission(mission_id).get_result()
                _lock_if_paid(mission_id)
        elif verdict == "allow":
            op = f"pay:{mission_id}"
            r = gw.create_payment(op, m["amount_minor"], m["currency"].lower())
            created = led.pay_once(op, mission_id, m["amount_minor"], m["currency"], gw.mode, r.provider_ref)
            if created:
                led.audit_note(mission_id, "system", f"payment {op} created ({gw.mode})",
                               {"operation_id": op, "amount_minor": m["amount_minor"],
                                "currency": m["currency"].lower(), "policy_hash": body["policy_hash"]},
                               kind="payment", mode=gw.mode, party=m["recipient"] or None,
                               stripe={"ids": [r.provider_ref], "livemode": False, "op": op})
            _lock_if_paid(mission_id)
        return body

    def _lock_if_paid(mission_id: str) -> None:
        """Once money moved, policies can no longer be attached/detached for this mission."""
        if led.payments_for(mission_id):
            reg = engine.registry
            reg.lock_target("mission", mission_id)
            reg.lock_target("payment", f"pay:{mission_id}")

    def fail(exc: Exception):
        r = error_response(exc)
        detail = json.loads(r.body)["error"]
        raise HTTPException(r.status_code, detail["message"]) from exc

    @app.get("/health")
    def health():
        return {"ok": True, "mode": gw.mode, "durable": durable}

    @app.post("/missions")
    def create(m: MissionIn):
        cur = (m.currency or org_currency(engine.registry) or default_currency(DEFAULT_POLICY)).lower()
        led.create_mission(m.id, m.amount_minor, cur, m.recipient)
        return decide(m.id)

    @app.post("/missions/{mission_id}/events")
    def event(mission_id: str, e: EventIn, request: Request):
        actor = actor_from(request)
        try:
            rev = e.expected_revision
            if e.inputs or (e.location is None and e.receipt is None):
                rev = engine.submit_inputs(mission_id, e.event_id, rev, e.inputs, actor)
            if e.location is not None:
                rev = engine.set_location(mission_id, f"{e.event_id}:loc", rev, e.location, actor)
            if e.receipt is not None:
                engine.confirm_receipt(mission_id, f"{e.event_id}:receipt", rev,
                                       recipient_id=e.receipt.recipient_id, nonce=e.receipt.nonce)
        except Exception as exc:  # noqa: BLE001
            fail(exc)
        return decide(mission_id)

    @app.post("/missions/{mission_id}/receipt-nonce")
    def receipt_nonce(mission_id: str):
        try:
            return {"nonce": engine.issue_receipt_nonce(mission_id), "recipient_id": f"recipient:{mission_id}"}
        except Exception as exc:  # noqa: BLE001
            fail(exc)

    @app.post("/missions/{mission_id}/approvals")
    def approvals(mission_id: str, a: ApprovalIn, request: Request):
        try:
            engine.approve(mission_id, a.event_id, a.expected_revision, actor_from(request),
                           amount_minor=a.amount_minor, currency=a.currency, recipient=a.recipient,
                           policy_hash=a.policy_hash)
        except Exception as exc:  # noqa: BLE001
            fail(exc)
        return decide(mission_id)

    @app.post("/missions/{mission_id}/overrides")
    def overrides(mission_id: str, o: OverrideIn, request: Request):
        try:
            engine.override(mission_id, o.event_id, o.expected_revision, actor_from(request),
                            clause_ids=o.clause_ids, reason=o.reason, policy_hash=o.policy_hash)
        except Exception as exc:  # noqa: BLE001
            fail(exc)
        return decide(mission_id)

    routers = [routes_jobs.router, routes_audit.router, routes_agent.router, routes_explain.router, routes_chat.router, routes_delivery.router, routes_fuel.router, routes_invoices.router,
               routes_policies.router, routes_refunds.router, routes_history.router, routes_author.router,
               routes_freeze.router, routes_pack.router]
    if durable:
        routers.append(workflows.router)
    api = APIRouter(prefix="/api")
    api.include_router(build_console_router(led, decide, lambda: gw.mode, engine))
    for r in routers:
        api.include_router(r)
        app.include_router(r)
    app.include_router(api)
    return app


app = create_app
