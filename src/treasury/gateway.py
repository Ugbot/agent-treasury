"""Payment gateway contract plus a deterministic in-memory MockGateway.

All amounts are integer minor units. Every mutating call takes an
``operation_id`` which must map to at most one provider effect (idempotency).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from .currency import DEFAULT_CURRENCY

Mode = Literal["mock", "stripe_test"]


class GatewayError(Exception):
    """A provider call failed (not a missing capability)."""


class LiveModeError(GatewayError):
    """A live credential or a livemode=true object was seen. Always fatal."""


class OutcomeUnknown(GatewayError):
    """The provider call may or may not have happened (timeout, 5xx). Retry with the SAME
    operation id: the idempotency key makes the retry return the original object."""


class CapabilityUnavailable(GatewayError):
    """A capability cannot be used; carries a specific setup instruction."""

    def __init__(self, capability: str, reason: str, setup: str):
        super().__init__(f"{capability} unavailable: {reason}")
        self.capability = capability
        self.reason = reason
        self.setup = setup


@dataclass(frozen=True)
class GatewayResult:
    provider_ref: str
    mode: Mode
    livemode: bool
    status: str
    amount_minor: int = 0
    currency: str = DEFAULT_CURRENCY
    operation_id: str = ""


@dataclass(frozen=True)
class PaymentResult(GatewayResult):
    charge_ref: str = ""  # ch_... : the source_transaction for later transfers
    transfer_group: str = ""


@dataclass(frozen=True)
class RefundResult(GatewayResult):
    payment_ref: str = ""


@dataclass(frozen=True)
class TransferResult(GatewayResult):
    destination: str = ""
    source_transaction: str = ""
    transfer_group: str = ""


@dataclass(frozen=True)
class CardResult(GatewayResult):
    last4: str = ""
    cardholder_ref: str = ""


@dataclass(frozen=True)
class FuelAuthResult(GatewayResult):
    card_ref: str = ""
    approved: bool = False


@dataclass(frozen=True)
class FuelCaptureResult(GatewayResult):
    authorization_ref: str = ""


@dataclass(frozen=True)
class FuelReleaseResult(GatewayResult):
    authorization_ref: str = ""


@dataclass(frozen=True)
class InvoiceResult(GatewayResult):
    hosted_url: str = ""


@runtime_checkable
class PaymentGateway(Protocol):
    mode: Mode

    def create_payment(self, operation_id: str, amount_minor: int, currency: str,
                       payment_method: str = "pm_card_visa",
                       transfer_group: str | None = None) -> PaymentResult: ...

    def refund(self, operation_id: str, payment_ref: str,
               amount_minor: int | None = None) -> RefundResult: ...

    def transfer(self, operation_id: str, amount_minor: int, currency: str,
                 destination: str, source_transaction: str | None = None,
                 transfer_group: str | None = None) -> TransferResult: ...

    def issue_card(self, operation_id: str, cardholder_name: str,
                   currency: str = DEFAULT_CURRENCY) -> CardResult: ...

    def authorize_fuel(self, operation_id: str, card_ref: str, amount_minor: int,
                       currency: str = DEFAULT_CURRENCY) -> FuelAuthResult: ...

    def capture_fuel(self, operation_id: str, authorization_ref: str,
                     amount_minor: int | None = None) -> FuelCaptureResult: ...

    def release_authorization(self, operation_id: str,
                              authorization_ref: str) -> FuelReleaseResult: ...

    def create_invoice(self, operation_id: str, amount_minor: int, currency: str,
                       customer_email: str, description: str = "") -> InvoiceResult: ...


def require_positive_minor(amount_minor: int) -> None:
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
        raise ValueError("amount_minor must be a positive integer (minor units)")


def _ref(prefix: str, operation_id: str, kind: str) -> str:
    return f"{prefix}_mock_{hashlib.sha256(f'{kind}:{operation_id}'.encode()).hexdigest()[:14]}"


@dataclass
class MockGateway:
    """Deterministic, idempotent, never touches the network."""

    mode: Mode = "mock"
    fail_authorizations_over_minor: int | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)
    _results: dict[tuple[str, str], GatewayResult] = field(default_factory=dict)

    def _once(self, kind: str, operation_id: str, build) -> GatewayResult:
        key = (kind, operation_id)
        self.calls.append(key)
        if key not in self._results:
            self._results[key] = build()
        return self._results[key]

    def _base(self, prefix, kind, op, status, amount=0, currency=DEFAULT_CURRENCY) -> dict:
        return {"provider_ref": _ref(prefix, op, kind), "mode": "mock", "livemode": False,
                "status": status, "amount_minor": amount, "currency": currency.lower(),
                "operation_id": op}

    def create_payment(self, operation_id, amount_minor, currency, payment_method="pm_card_visa",
                       transfer_group=None):
        require_positive_minor(amount_minor)
        return self._once("payment", operation_id, lambda: PaymentResult(
            **self._base("pi", "payment", operation_id, "succeeded", amount_minor, currency),
            charge_ref=_ref("ch", operation_id, "charge"), transfer_group=transfer_group or ""))

    def refund(self, operation_id, payment_ref, amount_minor=None):
        if amount_minor is not None:
            require_positive_minor(amount_minor)
        return self._once("refund", operation_id, lambda: RefundResult(
            **self._base("re", "refund", operation_id, "succeeded", amount_minor or 0),
            payment_ref=payment_ref))

    def transfer(self, operation_id, amount_minor, currency, destination,
                 source_transaction=None, transfer_group=None):
        require_positive_minor(amount_minor)
        return self._once("transfer", operation_id, lambda: TransferResult(
            **self._base("tr", "transfer", operation_id, "paid", amount_minor, currency),
            destination=destination, source_transaction=source_transaction or "",
            transfer_group=transfer_group or ""))

    def issue_card(self, operation_id, cardholder_name, currency=DEFAULT_CURRENCY):
        return self._once("card", operation_id, lambda: CardResult(
            **self._base("ic", "card", operation_id, "active", 0, currency),
            last4="4242", cardholder_ref=_ref("ich", operation_id, "cardholder")))

    def authorize_fuel(self, operation_id, card_ref, amount_minor, currency=DEFAULT_CURRENCY):
        require_positive_minor(amount_minor)
        cap = self.fail_authorizations_over_minor
        ok = cap is None or amount_minor <= cap
        return self._once("auth", operation_id, lambda: FuelAuthResult(
            **self._base("iauth", "auth", operation_id,
                         "pending" if ok else "closed", amount_minor, currency),
            card_ref=card_ref, approved=ok))

    def capture_fuel(self, operation_id, authorization_ref, amount_minor=None):
        if amount_minor is not None:
            require_positive_minor(amount_minor)
        return self._once("capture", operation_id, lambda: FuelCaptureResult(
            **self._base("ipi", "capture", operation_id, "captured", amount_minor or 0),
            authorization_ref=authorization_ref))

    def release_authorization(self, operation_id, authorization_ref):
        return self._once("release", operation_id, lambda: FuelReleaseResult(
            **self._base("iauth", "release", operation_id, "reversed"),
            authorization_ref=authorization_ref))

    def create_invoice(self, operation_id, amount_minor, currency, customer_email, description=""):
        require_positive_minor(amount_minor)
        ref = _ref("in", operation_id, "invoice")
        return self._once("invoice", operation_id, lambda: InvoiceResult(
            **self._base("in", "invoice", operation_id, "paid", amount_minor, currency),
            hosted_url=f"https://mock.invalid/invoice/{ref}"))
