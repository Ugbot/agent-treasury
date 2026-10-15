"""Why-was-this-paid: GET /payments/{id}/explanation (payment id = operation id such as pay:<mission>,
settle:<order>:<leg>, or a mission id).

Built from the audit log's decision entries (kind 'decision') and, for scenario and agent missions,
the agent's verdict entries (kind 'agent.verdict'). If the memory backend (Cognee) actually returns
text, `source` is 'cognee'; otherwise it is 'audit_log' and says so. Never labelled Cognee unless it was.

Scoped like the rest of /audit (demo auth, X-Party-Role / X-Party-Id): admin sees every entry; a
party sees only entries of its own party, and a payment with none of them is a 404.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from . import routes_policies
from .routes_audit import AuthError, caller_of, get_audit, scope_entry

router = APIRouter(tags=["explain"])

VERDICT_KINDS = ("decision", "agent.verdict")
_LEG_KIND = {"merchant": "pay_merchant", "driver": "pay_driver"}


def _resolve_mission(request: Request, audit, payment_id: str) -> str:
    db = request.app.state.ledger.db
    row = db.execute("SELECT mission_id FROM payments WHERE operation_id=?", (payment_id,)).fetchone()
    if row:
        return row["mission_id"]
    for e in reversed(audit.query(kind="stripe.call", limit=100000)):  # operation seen only in the audit log
        if (e.get("stripe") or {}).get("op") == payment_id and e.get("mission_id"):
            return e["mission_id"]
    return payment_id.removeprefix("pay:")


def _leg_kind(payment_id: str) -> str | None:
    if payment_id.startswith("settle:"):
        return _LEG_KIND.get(payment_id.rsplit(":", 1)[-1])
    return None


def _pick(verdicts: list[dict], leg_kind: str | None) -> dict:
    """The verdict that explains this payment: its own leg if known, preferring one that let money
    move (approved/allow), else the latest."""
    def kind_of(e):
        return ((e.get("payload") or {}).get("proposal") or {}).get("kind")

    pool = [e for e in verdicts if leg_kind and kind_of(e) == leg_kind] or verdicts
    ok = [e for e in pool if e["kind"] == "agent.verdict" and (e.get("payload") or {}).get("executed")
          or (e["kind"] == "decision" and (e.get("policy") or {}).get("verdict") == "allow")]
    return (ok or pool)[-1]


@router.get("/payments/{payment_id}/explanation")
def explanation(payment_id: str, request: Request):
    try:
        caller = caller_of(request)
    except AuthError as e:
        return e.response
    audit = get_audit(request.app.state)
    mission_id = _resolve_mission(request, audit, payment_id)
    party = None if caller.admin else caller.party_id
    entries = audit.query(mission_id=mission_id, party=party, limit=1000)
    verdicts = [e for e in entries if e["kind"] in VERDICT_KINDS]
    if not verdicts:
        raise HTTPException(404, "no decision recorded for this payment")
    leg = _leg_kind(payment_id)
    if leg and not any(((e.get("payload") or {}).get("proposal") or {}).get("kind") == leg for e in verdicts):
        raise HTTPException(404, "no decision recorded for this payment")  # e.g. another party's leg
    last = _pick(verdicts, leg)
    pol = last["policy"] or {}
    payload = last["payload"] or {}
    stripe_ids: list[str] = []
    livemode = False
    for e in entries:
        s = e.get("stripe")
        if s:
            stripe_ids += [i for i in s.get("ids", []) if i and i not in stripe_ids]
            livemode = livemode or bool(s.get("livemode"))
    source, note, text = "audit_log", "Answer is from the audit log.", last["summary"]
    if caller.admin:  # the memory backend is not party-scoped, so only admins get its text
        mem = routes_policies.get_registry().memory
        if hasattr(mem, "explain"):
            ex = mem.explain(mission_id, f"Why was payment {payment_id} allowed, held or denied?")
            source, note = ex.source, ex.note or note
            if ex.source == "cognee" and ex.evidence:
                text = "\n".join(ex.evidence)
    evidence = payload.get("facts") or {k: payload[k] for k in ("proposal", "plan_gate", "reasons", "status",
                                                                  "human_approved") if k in payload}
    clauses = [{"policy_id": c.get("policy_id", pol.get("id")), "policy_version": c.get("policy_version", pol.get("version")),
                "clause": c.get("clause"), "outcome": c.get("outcome"), "evidence": c.get("evidence")}
               for c in pol.get("clauses", [])]
    return {"verdict": pol.get("verdict"), "mode": last["mode"], "source": source, "sourceNote": note,
            "text": text, "mission_id": mission_id, "payment_id": payment_id,
            "decision_kind": last["kind"],
            "policy": {"id": pol.get("id"), "version": pol.get("version"), "hash": pol.get("hash")},
            "clauses": clauses, "evidence": evidence,
            "stripe": {"ids": stripe_ids, "livemode": livemode},
            "auditSeqs": [d["seq"] for d in verdicts],
            "entries": [scope_entry(e, caller) for e in verdicts] if not caller.admin else None}
