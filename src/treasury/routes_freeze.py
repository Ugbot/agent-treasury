"""Emergency payment freeze. Implemented as a new version of the mandatory org policy 'org-freeze'
(payment_freeze clause). Freezing is open to anyone who can call it; unfreezing is approver-only and
audit-logged with a reason. Mount `routes_freeze.router` like the other routers."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .decision import APPROVER_ROLES
from .policies import ALLOW_FREEZE_TOGGLE, PolicyError, PolicyRegistry
from .policy import Clause
from .routes_console import actor_from, err, ok
from .routes_policies import get_registry

router = APIRouter(tags=["freeze"])
FREEZE_ID = "org-freeze"
CLAUSE_ID = "FREEZE-1"


def seed_freeze(reg: PolicyRegistry) -> None:
    """Idempotent: publish org-freeze (inactive) and attach it as a mandatory org policy."""
    try:
        reg.get(FREEZE_ID)
        return
    except PolicyError:
        pass
    b = reg.publish(FREEZE_ID, "Payment freeze", [Clause(CLAUSE_ID, "Payments are frozen when active",
                                                         "payment_freeze", {"active": False})])
    reg.attach("organisation", "org", FREEZE_ID, b.version, reg.target_version("organisation", "org"),
               mandatory=True)


def _state(reg: PolicyRegistry, audit) -> dict:
    seed_freeze(reg)
    att = [a for a in reg.attachments_for("organisation", "org") if a.policy_id == FREEZE_ID]
    b = reg.get(FREEZE_ID, att[0].policy_version) if att else reg.get(FREEZE_ID)
    active = bool(b.clauses[0].params["active"])
    since = by = reason = None
    if audit is not None:
        rows = audit.query(kind="policy.freeze", limit=10000)
        if rows:
            last = rows[-1]
            since, by = last["ts"], last["actor"]
            reason = (last.get("payload") or {}).get("reason")
    return {"active": active, "version": b.version, "since": since, "by": by, "reason": reason}


@router.get("/freeze")
def get_freeze(request: Request):
    return ok(_state(get_registry(), getattr(request.app.state, "audit", None)))


@router.post("/freeze")
async def set_freeze(request: Request):
    actor = actor_from(request)
    if actor.role not in APPROVER_ROLES:
        return err(403, "forbidden", "only an approver can freeze or unfreeze payments")
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        b = None
    if (not isinstance(b, dict) or not isinstance(b.get("active"), bool)
            or not isinstance(b.get("reason"), str) or not b["reason"].strip()):
        return err(422, "bad_request", "active (boolean) and reason (non-empty string) are required")
    reg, audit = get_registry(), getattr(request.app.state, "audit", None)
    cur = _state(reg, audit)
    if cur["active"] != b["active"]:
        tok = ALLOW_FREEZE_TOGGLE.set(True)
        try:
            nb = reg.publish(FREEZE_ID, "Payment freeze",
                             [Clause(CLAUSE_ID, "Payments are frozen when active", "payment_freeze",
                                     {"active": b["active"]})], cur["version"])
            reg.attach("organisation", "org", FREEZE_ID, nb.version, reg.target_version("organisation", "org"),
                       mandatory=True)
        except PolicyError as e:
            return err(409, "conflict", str(e))
        finally:
            ALLOW_FREEZE_TOGGLE.reset(tok)
    if audit is not None:
        audit.append(actor.id, "policy.freeze",
                     f"payments {'frozen' if b['active'] else 'unfrozen'} by {actor.id}: {b['reason'].strip()}",
                     mission_id="org", party="org", payload={"active": b["active"], "reason": b["reason"].strip(),
                                           "role": actor.role})
    return ok(_state(reg, audit))
