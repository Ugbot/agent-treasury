"""Durable mission workflow on DBOS (SQLite system database).

One workflow per mission with a stable ID. The workflow waits for input events,
evaluates policy against a checkpointed snapshot, and pays via the saga store.
All non-deterministic reads (ledger, policy, provider) happen inside steps.
"""
from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from dbos import DBOS, DBOSConfig, SetWorkflowID
from fastapi import APIRouter, HTTPException

from .ledger import Ledger
from .policy import DEFAULT_POLICY, Policy
from .saga import (
    GatewayUnavailable,
    MockGateway,
    PaymentGateway,
    SagaStore,
    payment_operation_id,
)

INPUT_TOPIC = "input"


def workflow_id(mission_id: str) -> str:
    return f"mission:{mission_id}"


@dataclass
class Runtime:
    ledger: Ledger
    gateway: PaymentGateway = field(default_factory=MockGateway)
    policy: Policy = DEFAULT_POLICY
    wait_seconds: float = 5.0
    max_waits: int = 1000
    # decider(mission_id, snapshot) -> decision body. None: a DecisionEngine over the policy
    # registry (org-base + attached policies), never DEFAULT_POLICY alone.
    decider: Any = None
    _saga: SagaStore | None = None

    @property
    def saga(self) -> SagaStore:
        if self._saga is None:
            self._saga = SagaStore(self.ledger)
        return self._saga


_runtime: Runtime | None = None


def runtime() -> Runtime:
    if _runtime is None:
        raise RuntimeError("workflows not configured: call start_runtime(...) first")
    return _runtime


def start_runtime(ledger: Ledger, sys_db_path: str, gateway: PaymentGateway | None = None, **kw: Any) -> Runtime:
    """Configure and launch DBOS with a persistent SQLite system DB. Restart-safe:
    pending workflows are recovered on launch."""
    global _runtime
    _runtime = Runtime(ledger=ledger, gateway=gateway or MockGateway(), **kw)
    cfg: DBOSConfig = {
        "name": "agent-treasury",
        "system_database_url": f"sqlite:///{os.path.abspath(sys_db_path)}",
        "run_migrations": True,
    }
    with contextlib.suppress(Exception):  # a prior app/test may have left DBOS launched; start clean
        DBOS.destroy(destroy_registry=False)
    DBOS(config=cfg)
    DBOS.launch()
    return _runtime


def stop_runtime() -> None:
    """Shut DBOS down without discarding registered workflows (simulates process exit)."""
    DBOS.destroy(destroy_registry=False)


# ---- steps (checkpointed) ----

@DBOS.step()
def load_snapshot(mission_id: str) -> dict:
    m = runtime().ledger.mission(mission_id)
    if m is None:
        raise KeyError(mission_id)
    return {"amount_minor": m["amount_minor"], "currency": m["currency"],
            "revision": m["revision"], "inputs": {**json.loads(m["inputs"]), "currency": m["currency"]}}


@DBOS.step()
def decide_step(mission_id: str, snap: dict) -> dict:
    rt = runtime()
    if rt.decider is None:
        from .decision import DecisionEngine
        rt.decider = DecisionEngine(rt.ledger, mode=lambda: rt.gateway.mode).decide
    return rt.decider(mission_id, snap)


@DBOS.step()
def reserve_step(mission_id: str, snap: dict) -> dict:
    rt = runtime()
    return rt.saga.reserve(payment_operation_id(mission_id), mission_id, "payment",
                           snap["amount_minor"], snap["currency"], rt.gateway.mode)


@DBOS.step()
def pay_step(operation_id: str) -> dict:
    rt = runtime()
    try:
        op = rt.saga.execute(operation_id, rt.gateway)
    except GatewayUnavailable as exc:
        rt.ledger.audit_note(op_mission(rt, operation_id), "system", f"payment {operation_id} unavailable: {exc}",
                             {"operation_id": operation_id, "state": "unavailable"}, kind="payment",
                             mode=rt.gateway.mode)
        return {"state": "unavailable", "message": str(exc), "operation_id": operation_id}
    rt.ledger.audit_note(op["mission_id"], "stripe" if op["mode"] == "stripe_test" else "system",
                         f"payment {operation_id} {op['state']}",
                         {"operation_id": operation_id, "state": op["state"], "amount_minor": op["amount_minor"],
                          "currency": op["currency"].lower()},
                         kind="payment", mode=op["mode"],
                         stripe={"ids": [op["provider_ref"]] if op["provider_ref"] else [], "livemode": False,
                                 "op": operation_id})
    return {"state": op["state"], "provider_ref": op["provider_ref"], "mode": op["mode"],
            "operation_id": operation_id}


def op_mission(rt: Runtime, operation_id: str) -> str | None:
    op = rt.saga.get(operation_id)
    return op["mission_id"] if op else None


@DBOS.step()
def compensate_step(operation_id: str, policy_permits: bool) -> dict:
    rt = runtime()
    op = rt.saga.compensate(operation_id, rt.gateway, policy_permits=policy_permits)
    rt.ledger.audit_note(op["mission_id"], "system", f"compensation of {operation_id}: {op['state']}",
                         {"operation_id": operation_id, "state": op["state"], "policy_permits": policy_permits},
                         kind="saga.compensation", mode=op["mode"])
    return {"state": op["state"], "operation_id": operation_id, "evidence": op["evidence"]}


# ---- workflows ----

@DBOS.workflow()
def mission_workflow(mission_id: str) -> dict:
    rt = runtime()
    waits = 0
    last: dict = {}
    while True:
        snap = load_snapshot(mission_id)
        decision = decide_step(mission_id, snap)
        last = {"mission_id": mission_id, "verdict": decision["verdict"], "decision": decision}
        if decision["verdict"] == "deny":
            return {**last, "status": "denied"}
        if decision["verdict"] == "allow":
            op = reserve_step(mission_id, snap)
            paid = pay_step(op["operation_id"])
            if paid["state"] == "confirmed":
                return {**last, "status": "paid", "payment": paid}
            # failed/unavailable/unknown: never infer success; surface for retry/escalation
            return {**last, "status": "payment_" + paid["state"], "payment": paid}
        waits += 1
        if waits > rt.max_waits:
            return {**last, "status": "timed_out"}
        DBOS.recv(INPUT_TOPIC, timeout_seconds=rt.wait_seconds)  # checkpointed wake-up


@DBOS.workflow()
def compensation_workflow(operation_id: str, policy_permits: bool = False) -> dict:
    return compensate_step(operation_id, policy_permits)


# ---- public helpers ----

def start_mission(mission_id: str):
    """Idempotent: same mission ID -> same workflow ID -> one execution."""
    with SetWorkflowID(workflow_id(mission_id)):
        return DBOS.start_workflow(mission_workflow, mission_id)


def deliver_event(mission_id: str, event_id: str) -> None:
    """Wake the mission. The event itself is already persisted in the ledger by the API;
    DBOS dedupes the message on event_id."""
    DBOS.send(workflow_id(mission_id), event_id, INPUT_TOPIC, idempotency_key=event_id)


def mission_status(mission_id: str) -> dict | None:
    h = DBOS.retrieve_workflow(workflow_id(mission_id))
    return {"workflow_id": h.get_workflow_id(), "status": h.get_status().status}


router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("/missions/{mission_id}/start")
def api_start(mission_id: str):
    if runtime().ledger.mission(mission_id) is None:
        raise HTTPException(404, "unknown mission")
    h = start_mission(mission_id)
    return {"workflow_id": h.get_workflow_id()}


@router.post("/missions/{mission_id}/events/{event_id}")
def api_event(mission_id: str, event_id: str):
    deliver_event(mission_id, event_id)
    return {"delivered": True, "workflow_id": workflow_id(mission_id)}


@router.get("/missions/{mission_id}")
def api_status(mission_id: str):
    s = mission_status(mission_id)
    if s is None:
        raise HTTPException(404, "no workflow")
    return s
