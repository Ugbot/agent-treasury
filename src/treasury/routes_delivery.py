"""Delivery proof / settlement routes. Integrator mounts `router`."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .gateway import CapabilityUnavailable, GatewayError, LiveModeError, MockGateway
from .geo import Point
from .proof import PolicyDenied, ProofError, SettlementService, balance_view, seed_order

router = APIRouter(prefix="/delivery", tags=["delivery"])
_service: SettlementService | None = None


def get_service() -> SettlementService:
    global _service
    if _service is None:
        _service = SettlementService(MockGateway())
    return _service


def set_service(svc: SettlementService | None) -> None:
    global _service
    _service = svc


class SeedIn(BaseModel):
    order_id: str = "ord_1"
    destination: tuple[float, float] = (51.5, -0.12)


class NonceIn(BaseModel):
    now: float


class ReleaseIn(BaseModel):
    driver_id: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    position_ts: float
    recipient_id: str
    nonce: str
    now: float
    assignment_version: int | None = None


class ApproveIn(BaseModel):
    leg: str
    approver: str


def _call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except PolicyDenied as e:
        raise HTTPException(403, e.info) from e
    except ProofError as e:
        raise HTTPException(404 if "unknown order" in str(e) else 400, str(e)) from e
    except CapabilityUnavailable as e:
        raise HTTPException(503, {"unavailable": e.reason, "setup": e.setup}) from e
    except LiveModeError as e:
        raise HTTPException(502, f"blocked: {e}") from e
    except GatewayError as e:
        raise HTTPException(502, str(e)) from e


@router.post("/orders")
def seed(body: SeedIn):
    o = get_service().add_order(seed_order(body.order_id, destination=Point(*body.destination)))
    return {"order_id": o.id, "balance": balance_view(o)}


@router.post("/orders/{order_id}/receipt-nonce")
def nonce(order_id: str, body: NonceIn):
    return {"nonce": _call(get_service().issue_nonce, order_id, body.now), "expires_in_s": 120}


@router.post("/orders/{order_id}/fund")
def fund(order_id: str):
    """Charge the customer on the platform; the charge is the source_transaction of both legs."""
    return _call(get_service().fund_order, order_id)


@router.post("/orders/{order_id}/release")
def release(order_id: str, b: ReleaseIn):
    return _call(get_service().release, order_id, driver_id=b.driver_id,
                 position=Point(b.lat, b.lon), position_ts=b.position_ts,
                 recipient_id=b.recipient_id, nonce=b.nonce, now=b.now,
                 assignment_version=b.assignment_version)


@router.post("/orders/{order_id}/approve")
def approve(order_id: str, b: ApproveIn):
    return _call(get_service().approve, order_id, b.leg, b.approver)


@router.post("/orders/{order_id}/retry")
def retry(order_id: str):
    return _call(get_service().retry, order_id)


@router.get("/orders/{order_id}")
def status(order_id: str):
    return _call(get_service().get, order_id)
