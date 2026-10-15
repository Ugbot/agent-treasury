"""Invoice routes. Integrator mounts `router`; configure with set_service()."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .currency import DEFAULT_CURRENCY
from .gateway import CapabilityUnavailable, LiveModeError
from .invoices import (
    CollectionConflict,
    InvoiceError,
    InvoiceService,
    MockInvoiceProvider,
    NotFunded,
    PaymentsFrozen,
    SignatureError,
    StripeInvoiceProvider,
)

router = APIRouter(prefix="/invoices", tags=["invoices"])
_service: InvoiceService | None = None


def get_service() -> InvoiceService:
    global _service
    if _service is None:
        secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
        if os.environ.get("STRIPE_SECRET_KEY"):
            provider = StripeInvoiceProvider()
        else:
            provider = MockInvoiceProvider()
        _service = InvoiceService(provider, os.environ.get("TREASURY_INVOICE_DB", ":memory:"),
                                  webhook_secret=secret)
    return _service


def set_service(svc: InvoiceService | None) -> None:
    global _service
    _service = svc


def _unavailable(e: CapabilityUnavailable) -> HTTPException:
    return HTTPException(503, {"unavailable": e.capability, "reason": e.reason, "setup": e.setup})


class InvoiceIn(BaseModel):
    obligation_id: str
    amount_minor: int = Field(gt=0)
    currency: str = DEFAULT_CURRENCY.upper()
    customer_email: str
    threshold_minor: int = Field(ge=0)
    description: str = ""


@router.post("")
def create(body: InvoiceIn):
    try:
        s = get_service().ensure_invoice(body.obligation_id, body.amount_minor, body.currency,
                                         body.customer_email, body.threshold_minor,
                                         body.description)
    except CapabilityUnavailable as e:
        raise _unavailable(e)
    except CollectionConflict as e:
        raise HTTPException(409, str(e))
    except LiveModeError as e:
        raise HTTPException(403, str(e))
    if s is None:
        return {"invoiced": False, "reason": "amount not above threshold; ordinary collection"}
    return {"invoiced": True, **s.public()}


@router.get("/{obligation_id}")
def read(obligation_id: str):
    s = get_service().get(obligation_id)
    if s is None:
        raise HTTPException(404, "no invoice for obligation")
    return s.public()


@router.get("/{obligation_id}/gate")
def gate(obligation_id: str):
    try:
        return {"funded": True, **get_service().require_funded(obligation_id).public()}
    except NotFunded as e:
        raise HTTPException(402, str(e))


@router.post("/{obligation_id}/poll")
def poll(obligation_id: str):
    try:
        return get_service().poll(obligation_id).public()
    except InvoiceError as e:
        raise HTTPException(404, str(e))
    except CapabilityUnavailable as e:
        raise _unavailable(e)


@router.post("/{obligation_id}/sandbox-pay")
def sandbox_pay(obligation_id: str, request: Request):
    """TEST helper: Stripe pays the invoice; result reflects verified provider state."""
    try:
        svc = get_service()
        if svc.audit is None:
            svc.audit = getattr(request.app.state, "audit", None)
        return svc.sandbox_pay(obligation_id).public()
    except PaymentsFrozen as e:
        raise HTTPException(409, str(e))
    except InvoiceError as e:
        raise HTTPException(404, str(e))
    except CapabilityUnavailable as e:
        raise _unavailable(e)


@router.post("/webhooks/stripe")
async def webhook(request: Request):
    payload = await request.body()
    try:
        return get_service().handle_webhook(payload, request.headers.get("stripe-signature", ""))
    except CapabilityUnavailable as e:
        raise _unavailable(e)
    except SignatureError as e:
        raise HTTPException(400, str(e))
    except LiveModeError as e:
        raise HTTPException(403, str(e))
