"""Console routes for the browser (docs/API_CONTRACT.md): {data}/{error} envelope, commands, snapshot.

The browser only sends commands. Safety facts (receipt, location/zone) are produced by the
server (decision.py -> proof.py / geo.py); approvals and overrides need an approver actor
(X-Actor-Id / X-Actor-Role headers; there is no authentication in the demo, so the role is an
assertion that is recorded in the audit log)."""
from __future__ import annotations

import json
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .decision import (
    Actor,
    ApprovalError,
    DecisionEngine,
    InputError,
    ProofRejected,
    org_currency,
)
from .ledger import AmountLockedError, LatchedFactError, Ledger, StaleRevisionError
from .policies import NotFoundError, PolicyError
from .policy import DEFAULT_POLICY, default_currency
from .saga import refundable_payment

SCENARIOS = [
    {"id": "happy-path", "title": "Delivery confirmed, within policy"},
    {"id": "prohibited-zone", "title": "Route enters a prohibited zone"},
    {"id": "over-limit", "title": "Amount above approval threshold"},
    {"id": "deliver_and_release", "title": "Delivery proven, merchant and driver paid (merchant leg needs approval)"},
    {"id": "fuel_entitlement", "title": "Capped fuel allowance for the assigned driver, revoked when coverage lapses"},
    {"id": "coverage_replan", "title": "Coverage changes mid-job: replan, block the stale plan, escalate if none"},
    {"id": "cancel_refund", "title": "Cancel before pickup refunds; cancel after pickup escalates"},
]


def ok(data, status=200):
    return JSONResponse({"data": data}, status_code=status)


def err(status: int, code: str, message: str, **extra):
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status)


def actor_from(request: Request, body: dict | None = None) -> Actor:
    hb = (body or {}).get("actor") if isinstance((body or {}).get("actor"), dict) else {}
    return Actor(request.headers.get("x-actor-id") or hb.get("id") or "anonymous",
                 (request.headers.get("x-actor-role") or hb.get("role") or "client").lower())


def error_response(exc: Exception):
    """Map domain exceptions to the console error envelope."""
    if isinstance(exc, KeyError):
        return err(404, "not_found", "unknown mission")
    if isinstance(exc, StaleRevisionError):
        return err(409, "stale_revision", str(exc))
    if isinstance(exc, LatchedFactError):
        return err(409, "latched_fact", str(exc))
    if isinstance(exc, AmountLockedError):
        return err(409, "amount_immutable", str(exc))
    if isinstance(exc, ProofRejected):
        return err(422, "receipt_rejected", str(exc), gates=exc.gates, reasons=exc.reasons)
    if isinstance(exc, (InputError, ApprovalError)):
        return err(exc.status, exc.code, str(exc))
    if isinstance(exc, PolicyError):
        from .routes_policies import _http
        h = _http(exc)
        return err(h.status_code, "policy_error", str(exc))
    if isinstance(exc, ValueError):
        return err(422, "bad_request", str(exc))
    raise exc


def build_console_router(led: Ledger, decide: Callable[[str], dict], mode: Callable[[], str],
                         engine: DecisionEngine | None = None) -> APIRouter:
    router = APIRouter(tags=["console"])
    eng = engine or DecisionEngine(led, mode=mode)

    def default_cur() -> str:
        try:
            return org_currency(eng.registry) or default_currency(DEFAULT_POLICY)
        except Exception:  # noqa: BLE001
            return default_currency(DEFAULT_POLICY)

    def current_id() -> str:
        row = led.db.execute("SELECT id FROM missions ORDER BY rowid DESC LIMIT 1").fetchone()
        if row:
            return row["id"]
        led.create_mission("demo-1", 2000, default_cur())
        eng.init_mission_facts("demo-1", "demo-1:init", "happy-path")
        return "demo-1"

    def resolve(mid: str) -> str:
        return current_id() if mid == "current" else mid

    def clause_info(reg, policy_id, version, clause_id):
        try:
            b = reg.get(policy_id, version)
        except Exception:  # noqa: BLE001
            return None
        return next((c for c in b.clauses if c.id == clause_id), None)

    def snapshot(mid: str, body: dict) -> dict | None:
        m = led.mission(mid)
        if m is None:
            return None
        reg = eng.registry
        inputs = json.loads(m["inputs"])
        clauses = []
        for r in body["results"]:
            c = clause_info(reg, r.get("policy_id"), r.get("policy_version"), r.get("clause"))
            cid = r.get("clause") or f"{r.get('policy_id')}:pause"
            clauses.append({"clauseId": cid, "policyId": r.get("policy_id"),
                            "prose": c.prose if c else cid, "passed": r["outcome"] == "allow",
                            "outcome": r["outcome"],
                            "observed": json.dumps(r["evidence"]) if r["evidence"] else "ok",
                            "expected": c.kind if c else ""})
        decision = {"decisionId": f"{mid}:{body['input_revision']}", "verdict": body["verdict"],
                    "inputRevision": body["input_revision"], "effectivePolicyHash": body["policy_hash"],
                    "clauses": clauses, "obligations": [], "sticky": body["sticky"],
                    "approval": {"required": body["approval_required"], "approved": body["approval"] is not None,
                                 "bound": {"amountMinor": m["amount_minor"], "currency": m["currency"].lower(),
                                           "recipient": m["recipient"], "policyHash": body["policy_hash"]}}}
        pay = led.db.execute("SELECT * FROM payments WHERE mission_id=? LIMIT 1", (mid,)).fetchone()
        operation = None
        if pay:
            operation = {"operationId": pay["operation_id"], "kind": "payment", "amountMinor": pay["amount_minor"],
                         "currency": m["currency"], "status": "confirmed", "mode": pay["mode"],
                         "providerObjectId": pay["provider_ref"]}
        events = [{"eventId": e["id"], "type": "input", "simTime": e["revision"],
                   "description": e["payload"], "mode": mode(), "source": "simulation"}
                  for e in led.db.execute("SELECT * FROM events WHERE mission_id=? ORDER BY revision", (mid,))]
        policies = []
        for att_id, pid, ver, mandatory in body["attachments"]:
            try:
                h = reg.get(pid, ver).as_policy().hash
            except NotFoundError:
                h = ""
            policies.append({"policyId": pid, "version": ver, "hash": h, "mandatory": bool(mandatory),
                             "attachmentId": att_id})
        return {"revision": m["revision"], "mode": mode(), "missionId": mid,
                "scenario": inputs.get("scenario", "happy-path"),
                "inputs": {"amountMinor": m["amount_minor"], "currency": m["currency"],
                           "insured": bool(inputs.get("insured", True)),
                           "location": inputs.get("location", "depot"),
                           "receiptConfirmed": inputs.get("receipt_confirmed") is True,
                           "prohibitedZone": inputs.get("prohibited_zone") is True,
                           "recipient": m["recipient"]},
                "policies": policies, "decision": decision, "operation": operation, "events": events}

    @router.get("/state")
    def state(missionId: str = "current"):
        mid = resolve(missionId)
        if led.mission(mid) is None:
            return err(404, "not_found", "unknown mission")
        body = decide(mid)  # idempotent: re-evaluates against the live registry, pays at most once
        return ok(snapshot(mid, body))

    @router.get("/scenarios")
    def scenarios():
        return ok(SCENARIOS)

    @router.get("/missions/{mission_id}/payment-state")
    def payment_state(mission_id: str):
        """Real payment/transfer state from the saga/ledger; refund code must read this, never a client order."""
        mid = resolve(mission_id)
        if led.mission(mid) is None:
            return err(404, "not_found", "unknown mission")
        return ok(refundable_payment(led, mid))

    @router.post("/missions/{mission_id}/receipt-nonce")
    def receipt_nonce(mission_id: str):
        mid = resolve(mission_id)
        try:
            return ok({"nonce": eng.issue_receipt_nonce(mid), "recipientId": f"recipient:{mid}"})
        except Exception as exc:  # noqa: BLE001
            return error_response(exc)

    @router.post("/missions")
    async def select_scenario(request: Request):
        body = await request.json()
        scenario = body.get("scenario", "happy-path")
        if scenario not in {s["id"] for s in SCENARIOS}:
            return err(422, "bad_scenario", "unknown scenario")
        n = led.db.execute("SELECT COUNT(*) FROM missions").fetchone()[0] + 1
        mid = f"demo-{n}"
        amount = 9000 if scenario == "over-limit" else 2000
        led.create_mission(mid, amount, default_cur())
        eng.init_mission_facts(mid, f"{body.get('commandId', mid)}:init", scenario,
                               location="prohibited" if scenario == "prohibited-zone" else "depot")
        decide(mid)
        return ok({"accepted": True, "revision": led.mission(mid)["revision"], "commandId": body.get("commandId", mid)})

    @router.post("/missions/{mission_id}/events")
    async def command(mission_id: str, request: Request):
        mid = resolve(mission_id)
        body = await request.json()
        t, p = body.get("type"), body.get("payload") or {}
        actor = actor_from(request, body)
        if led.mission(mid) is None:
            return err(404, "not_found", "unknown mission")
        eid = body.get("eventId", "")
        try:
            rev = int(body.get("expectedRevision", -1))
        except (TypeError, ValueError):
            return err(422, "bad_revision", "expectedRevision must be an integer")
        try:
            if t == "set_amount":
                amount = p.get("amountMinor")
                if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                    return err(422, "bad_amount", "amount must be a positive integer in minor units")
                eng.set_amount(mid, eid, rev, amount, actor)
            elif t == "set_insurance":
                covered = p.get("covered")
                if not isinstance(covered, bool):
                    return err(422, "bad_type", "covered must be a boolean")
                eng.submit_inputs(mid, eid, rev, {"insured": covered}, actor)
            elif t == "set_location":
                eng.set_location(mid, eid, rev, str(p.get("location", "")), actor)
            elif t == "confirm_receipt":
                eng.confirm_receipt(mid, eid, rev, recipient_id=p.get("recipientId"), nonce=p.get("receiptNonce"))
            elif t == "attach_policy":
                pv = p.get("policyVersion")
                if not isinstance(pv, int) or isinstance(pv, bool) or not isinstance(p.get("policyId"), str):
                    return err(422, "bad_policy_ref", "policyId (string) and policyVersion (integer) are required")
                eng.attach_policy(mid, p["policyId"], pv, p.get("expectedTargetVersion"), actor)
            elif t == "approve":
                eng.approve(mid, eid, rev, actor, amount_minor=p.get("amountMinor"), currency=p.get("currency"),
                            recipient=p.get("recipient"), policy_hash=p.get("policyHash"))
            elif t == "override":
                eng.override(mid, eid, rev, actor, clause_ids=p.get("clauseIds"), reason=p.get("reason"),
                             policy_hash=p.get("policyHash"))
            else:
                return err(422, "unknown_command", f"unsupported command type {t!r}")
        except Exception as exc:  # noqa: BLE001
            return error_response(exc)
        decide(mid)
        return ok({"accepted": True, "revision": led.mission(mid)["revision"], "commandId": eid})

    return router
