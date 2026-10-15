"""Bitemporal history routes (admin/audit view; demo-auth scoping intentionally not applied).

Mount: app.include_router(router). Needs app.state.audit or app.state.ledger (see routes_audit.get_audit).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .bitemporal import PolicyTimeline, payment_history
from .routes_audit import get_audit

router = APIRouter(tags=["history"])


def _bad(msg: str, status: int = 400):
    return JSONResponse({"error": msg}, status_code=status)


def _inherited(raw: str | None) -> list[tuple[str, str]]:
    out = []
    for part in (raw or "").split(","):
        if ":" in part:
            t, i = part.split(":", 1)
            out.append((t.strip(), i.strip()))
    return out


@router.get("/history/policy-as-of")
def policy_as_of(request: Request, target_type: str, target_id: str, at: str, basis: str = "valid",
                 inherited: str | None = None):
    """Policy versions, hashes and attachments in force at `at` (ISO time). inherited=job:j1,card:c1."""
    if basis not in ("valid", "recorded"):
        return _bad("basis must be 'valid' or 'recorded'")
    try:
        return PolicyTimeline(get_audit(request.app.state)).policy_as_of(
            target_type, target_id, at, basis, _inherited(inherited))
    except ValueError as exc:
        return _bad(f"bad time: {exc}")


def _history(request: Request, ident: str):
    app = request.app.state
    return payment_history(get_audit(app), ident, ledger=getattr(app, "ledger", None))


@router.get("/history/payment/{payment_id}")
def history_payment(request: Request, payment_id: str):
    h = _history(request, payment_id)
    return {k: h[k] for k in ("mission_id", "requested_id", "summary", "timeline", "divergence")}


@router.get("/history/mission/{mission_id}")
def history_mission(request: Request, mission_id: str):
    """Full replay including both orderings (valid time and transaction time) and late arrivals."""
    return _history(request, mission_id)
