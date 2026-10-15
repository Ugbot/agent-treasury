"""Fuel allowance routes. Integrator mounts `router`."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .fuel import FuelService, SignatureError
from .gateway import CapabilityUnavailable, GatewayError, LiveModeError, MockGateway
from .geo import Point

router = APIRouter(prefix="/fuel", tags=["fuel"])
_service: FuelService | None = None


def get_service() -> FuelService:
    global _service
    if _service is None:
        _service = FuelService(MockGateway())
    return _service


def set_service(svc: FuelService | None) -> None:
    global _service
    _service = svc


class JobIn(BaseModel):
    status: str | None = None
    driver_id: str | None = None
    assignment_version: int | None = None
    coverage_valid: bool | None = None
    route: list[tuple[float, float]] | None = None
    position: tuple[float, float] | None = None
    position_ts: float | None = None


class IssueIn(BaseModel):
    job_id: str
    driver_id: str
    assignment_version: int


class AttemptIn(BaseModel):
    amount_minor: int = Field(gt=0)
    category: str
    country: str
    merchant_id: str


class CaptureIn(BaseModel):
    amount_minor: int | None = Field(default=None, gt=0)


def _view(svc: FuelService, eid: str) -> dict:
    e = svc.entitlements.get(eid)
    if e is None:
        raise HTTPException(404, "unknown entitlement")
    return {**{k: v for k, v in e.__dict__.items()}, "remaining_minor": e.remaining_minor,
            "reconcile": svc.reconcile(eid)}


@router.put("/jobs/{job_id}")
def put_job(job_id: str, body: JobIn):
    d = body.model_dump(exclude_none=True)
    if "route" in d:
        d["route"] = [Point(*p) for p in d["route"]]
    if "position" in d:
        d["position"] = Point(*d["position"])
    j = get_service().update_job(job_id, **d)
    return {"job_id": j.job_id, "status": j.status}


@router.post("/entitlements")
def issue(body: IssueIn):
    try:
        e = get_service().issue(body.job_id, body.driver_id, body.assignment_version)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return _view(get_service(), e.entitlement_id)


@router.get("/entitlements/{eid}")
def get_entitlement(eid: str):
    return _view(get_service(), eid)


@router.post("/entitlements/{eid}/revoke")
def revoke(eid: str):
    try:
        get_service().revoke(eid)
    except KeyError:
        raise HTTPException(404, "unknown entitlement")
    return _view(get_service(), eid)


@router.post("/entitlements/{eid}/attempts")
def attempt(eid: str, body: AttemptIn):
    svc = get_service()
    if eid not in svc.entitlements:
        raise HTTPException(404, "unknown entitlement")
    try:
        rec = svc.attempt(eid, body.amount_minor, body.category, body.country, body.merchant_id)
    except LiveModeError as exc:
        raise HTTPException(502, f"blocked: {exc}")
    return {**rec.__dict__, "reasons": list(rec.reasons)}


@router.post("/authorizations/{aid}/capture")
def capture(aid: str, body: CaptureIn):
    svc = get_service()
    if aid not in svc.auths:
        raise HTTPException(404, "unknown authorization")
    try:
        rec = svc.capture(aid, body.amount_minor)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except CapabilityUnavailable as exc:
        raise HTTPException(503, {"unavailable": exc.reason, "setup": exc.setup})
    except LiveModeError as exc:
        raise HTTPException(502, f"blocked: {exc}")
    except GatewayError as exc:
        raise HTTPException(502, str(exc))
    return {**rec.__dict__, "reasons": list(rec.reasons)}


@router.post("/authorizations/{aid}/release")
def release(aid: str):
    """Void an uncaptured authorization at the provider and free its reserved cap."""
    svc = get_service()
    if aid not in svc.auths:
        raise HTTPException(404, "unknown authorization")
    try:
        rec = svc.release(aid)
    except CapabilityUnavailable as exc:
        raise HTTPException(503, {"unavailable": exc.reason, "setup": exc.setup})
    except LiveModeError as exc:
        raise HTTPException(502, f"blocked: {exc}")
    except GatewayError as exc:
        raise HTTPException(502, str(exc))
    return {**rec.__dict__, "reasons": list(rec.reasons)}


@router.post("/authorizations/{aid}/resolve")
def resolve(aid: str):
    """Retry an authorization whose provider outcome was unknown (same idempotency key)."""
    svc = get_service()
    if aid not in svc.auths:
        raise HTTPException(404, "unknown authorization")
    rec = svc.resolve_unknown(aid)
    return {**rec.__dict__, "reasons": list(rec.reasons)}


@router.post("/webhook")
async def webhook(request: Request):
    payload = await request.body()
    try:
        return get_service().handle_webhook(
            payload, request.headers.get("stripe-signature"),
            os.environ.get("STRIPE_WEBHOOK_SECRET"))
    except CapabilityUnavailable as exc:
        raise HTTPException(503, {"unavailable": exc.reason, "setup": exc.setup})
    except SignatureError as exc:
        raise HTTPException(400, str(exc))
    except LiveModeError as exc:
        raise HTTPException(400, str(exc))
