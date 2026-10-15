"""Refund routes. Integrator mounts `router`."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .gateway import LiveModeError, MockGateway
from .refunds import NoPaymentState, RefundService, UnknownOrder

router = APIRouter(prefix="/refunds", tags=["refunds"])
_service: RefundService | None = None


def get_service() -> RefundService:
    global _service
    if _service is None:
        _service = RefundService(MockGateway())
    return _service


def set_service(svc: RefundService | None) -> None:
    global _service
    _service = svc


def _bound() -> RefundService:
    """Service bound to real state: falls back to the delivery settlement service."""
    svc = get_service()
    if not svc.has_state_source:
        from . import routes_delivery
        svc.bind(settlements=routes_delivery.get_service())
    return svc


class OrderIn(BaseModel):
    """Only the facts the client may assert. Amount, currency, payment reference, transfers and
    prior refunds are read from real payment/settlement state, never from this body."""
    order_id: str
    status: str = "funded"
    custody: str = "clear"
    outcome_uncertain: bool = False
    fuel_captured_minor: int = Field(0, ge=0)
    processing_fee_minor: int = Field(0, ge=0)


class RecoveryRef(BaseModel):
    id: str
    version: int


class CancelIn(BaseModel):
    cancel_event_id: str
    evidence: dict = {}
    recovery_policy: RecoveryRef | None = None


@router.post("/orders")
def register(o: OrderIn):
    try:
        return _bound().order_from_state(**o.model_dump()).__dict__
    except NoPaymentState:
        raise HTTPException(409, "no confirmed payment for this order; fund it first")


@router.post("/orders/{order_id}/cancel")
def cancel(order_id: str, c: CancelIn):
    try:
        return _bound().request_refund(order_id, c.cancel_event_id, c.evidence,
                                       c.recovery_policy.model_dump() if c.recovery_policy else None)
    except UnknownOrder:
        raise HTTPException(404, "unknown order")
    except LiveModeError as exc:
        raise HTTPException(422, str(exc))


@router.get("/orders/{order_id}")
def status(order_id: str):
    svc = _bound()
    try:
        order = svc.get_order(order_id)
    except UnknownOrder:
        raise HTTPException(404, "unknown order")
    return {"order": order.__dict__, "refund": svc.get(order_id),
            "remaining_refundable_minor": order.remaining_refundable_minor}


@router.post("/orders/{order_id}/reconcile")
def reconcile(order_id: str):
    try:
        return _bound().reconcile(order_id)
    except UnknownOrder:
        raise HTTPException(404, "unknown order")
