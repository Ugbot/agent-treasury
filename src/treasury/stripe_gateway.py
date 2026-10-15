"""Stripe TEST-mode gateway. Live keys and livemode=true objects are rejected.

Settlement model: separate charges and transfers. The customer is charged once on the
platform (optionally with a ``transfer_group``); each recipient then gets its own Transfer
carrying ``source_transaction`` (the charge) and the same ``transfer_group``. Every mutating
call uses a stable idempotency key derived from the operation id, and every call (success,
failure, unavailable, unknown) is written to the audit log when one is attached.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from treasury.gateway import (
    CapabilityUnavailable,
    CardResult,
    FuelAuthResult,
    FuelCaptureResult,
    FuelReleaseResult,
    GatewayError,
    InvoiceResult,
    LiveModeError,
    OutcomeUnknown,
    PaymentResult,
    RefundResult,
    TransferResult,
    require_positive_minor,
)

from .currency import DEFAULT_CURRENCY

SETUP = {
    "payments": "Set STRIPE_SECRET_KEY to a sandbox secret key (sk_test_...) from "
                "https://dashboard.stripe.com/test/apikeys.",
    "connect_transfers": "Enable Connect in the sandbox, then run `uv run python "
                "scripts/bootstrap_sandbox.py` (needs a standard sk_test_ key) to create two "
                "connected accounts with the 'transfers' capability active and fund the platform "
                "balance (pm_card_bypassPending). Or set STRIPE_CONNECT_ACCOUNT_A / "
                "STRIPE_CONNECT_ACCOUNT_B (acct_...) yourself.",
    "issuing": "Enable Issuing in the sandbox Dashboard (https://dashboard.stripe.com/test/issuing/"
               "overview), use a standard sk_test_ key, then run `uv run python "
               "scripts/bootstrap_sandbox.py` to top up the Issuing balance (Top-ups API, US).",
    "invoicing": "Invoicing is part of the base API; check the key has invoice and customer "
                 "write permissions (restricted keys: Invoices, Invoice Items, Customers).",
}

# Anything that looks like a Stripe credential, including short/masked forms Stripe echoes
# back in errors (e.g. "rkcs_te****abcd"), webhook secrets and PaymentIntent client secrets.
_SECRET_RE = re.compile(
    r"\b(?:sk|rk|rkcs|pk|ek)_(?:(?:test|live)_[A-Za-z0-9_*]+|[A-Za-z0-9]*\*[A-Za-z0-9_*]*)"
    r"|\bwhsec_[A-Za-z0-9_*]+"
    r"|\b(?:pi|seti|src|cs)_[A-Za-z0-9]+_secret_[A-Za-z0-9]+")
_SECRET_KEYS = {"client_secret", "number", "cvc", "secret", "api_key", "authorization"}


def redact(value: Any) -> Any:
    """Recursively mask secrets so evidence is safe to log or publish."""
    if isinstance(value, dict):
        return {k: "[redacted]" if str(k).lower() in _SECRET_KEYS else redact(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _SECRET_RE.sub("[redacted-key]", value)
    return value


def key_kind(key: str | None) -> str:
    """Classify a key by prefix only: standard | restricted | restricted_sandbox | live |
    missing | unknown. Never inspects or returns the secret part."""
    if not key:
        return "missing"
    if "_live_" in key[:12]:
        return "live"
    if key.startswith("sk_test_"):
        return "standard"
    if key.startswith("rkcs_test_"):
        return "restricted_sandbox"
    if key.startswith("rk_test_"):
        return "restricted"
    return "unknown"


def validate_test_key(key: str | None) -> str:
    """Return the key if it is a sandbox key, else raise. Never echoes the key."""
    kind = key_kind(key)
    if kind == "missing":
        raise CapabilityUnavailable("payments", "STRIPE_SECRET_KEY is not set", SETUP["payments"])
    if kind == "live":
        raise LiveModeError("live Stripe keys are rejected; this app is test mode only")
    if kind == "unknown":
        raise LiveModeError("unrecognised Stripe key format; only sk_test_/rk_test_/rkcs_test_ accepted")
    return key  # type: ignore[return-value]


def to_plain(obj: Any) -> Any:
    """Convert stripe-python objects (not dicts in v16) to plain dicts/lists."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return obj


def check_livemode(obj: Any) -> None:
    if isinstance(obj, dict):
        if obj.get("livemode") is True:
            raise LiveModeError("provider returned an object with livemode=true")
        for v in obj.values():
            if isinstance(v, (dict, list)):
                check_livemode(v)
    elif isinstance(obj, list):
        for v in obj:
            check_livemode(v)


_ACCESS_HINTS = ("not enabled", "issuing", "not have access", "capabilit", "permission",
                 "not allowed", "signed up", "not activated", "connect")


def _object_ids(obj: Any) -> list[str]:
    if not isinstance(obj, dict):
        return []
    ids = [obj["id"]] if isinstance(obj.get("id"), str) else []
    for k in ("latest_charge", "charge", "balance_transaction", "source_transaction",
              "destination", "card", "cardholder", "payment_intent", "transfer_group"):
        v = obj.get(k)
        if isinstance(v, dict):
            v = v.get("id")
        if isinstance(v, str) and v not in ids:
            ids.append(v)
    return ids


_AUDIT_CTX: contextvars.ContextVar[tuple[str | None, str | None]] = contextvars.ContextVar(
    "treasury_audit_ctx", default=(None, None))


@contextlib.contextmanager
def audit_scope(mission_id: str | None, party: str | None = None):
    """Callers wrap gateway calls in this so each stripe.call audit entry carries mission_id and party."""
    tok = _AUDIT_CTX.set((mission_id, party))
    try:
        yield
    finally:
        _AUDIT_CTX.reset(tok)


class StripeGateway:
    mode = "stripe_test"

    def __init__(self, api_key: str | None = None, client: Any = None, audit: Any = None):
        if client is None:
            key = validate_test_key(api_key if api_key is not None
                                    else os.environ.get("STRIPE_SECRET_KEY"))
            import stripe
            client = stripe.StripeClient(key)
            self.key_kind = key_kind(key)
        else:
            if api_key is not None:
                validate_test_key(api_key)
            self.key_kind = key_kind(api_key)
        self.client = client
        self.audit = audit

    def set_audit(self, audit: Any) -> None:
        self.audit = audit

    # -- plumbing ---------------------------------------------------------
    def _record(self, action: str, op: str, idem: str, outcome: str, ids: list[str],
                livemode: bool | None, detail: str = "") -> None:
        if self.audit is None:
            return
        mission_id, party = _AUDIT_CTX.get()
        self.audit.append(
            "stripe", "stripe.call", f"{action} -> {outcome}", mission_id=mission_id, party=party,
            payload={"action": action, "operation_id": op, "outcome": outcome,
                     "detail": redact(detail)[:300]},
            stripe={"ids": ids, "idempotency_key": idem, "livemode": livemode, "op": op,
                    "outcome": outcome},
            mode="stripe_test")

    def _call(self, capability: str, fn: Callable[[], Any], *, action: str = "",
              op: str = "", idem: str = "") -> Any:
        import stripe
        action = action or capability
        try:
            obj = fn()
        except stripe.AuthenticationError as e:
            self._record(action, op, idem, "unavailable", [], None, "api key rejected")
            raise CapabilityUnavailable(capability, "Stripe rejected the API key",
                                        SETUP["payments"]) from e
        except stripe.PermissionError as e:
            msg = str(redact(str(e)))[:200]
            self._record(action, op, idem, "unavailable", [], None, msg)
            raise CapabilityUnavailable(capability, msg,
                                        SETUP.get(capability, SETUP["payments"])) from e
        except stripe.InvalidRequestError as e:
            msg = str(redact(str(e)))
            if capability in ("issuing", "connect_transfers") and any(
                    h in msg.lower() for h in _ACCESS_HINTS):
                self._record(action, op, idem, "unavailable", [], None, msg)
                raise CapabilityUnavailable(capability, msg[:200], SETUP[capability]) from e
            self._record(action, op, idem, "failed", [], None, msg)
            raise GatewayError(msg[:300]) from e
        except stripe.APIConnectionError as e:
            msg = str(redact(str(e)))[:300]
            self._record(action, op, idem, "unknown", [], None, msg)
            raise OutcomeUnknown(f"outcome unknown (connection): {msg}") from e
        except stripe.StripeError as e:
            msg = str(redact(str(e)))[:300]
            status = getattr(e, "http_status", None)
            if isinstance(status, int) and status >= 500:
                self._record(action, op, idem, "unknown", [], None, msg)
                raise OutcomeUnknown(f"outcome unknown (HTTP {status}): {msg}") from e
            self._record(action, op, idem, "failed", [], None, msg)
            raise GatewayError(msg) from e
        obj = to_plain(obj)
        try:
            check_livemode(obj)
        except LiveModeError:
            self._record(action, op, idem, "livemode_rejected", _object_ids(obj), True)
            raise
        live = obj.get("livemode") if isinstance(obj, dict) else None
        self._record(action, op, idem, "ok", _object_ids(obj), bool(live) if live is not None else False,
                     str(obj.get("status") or "") if isinstance(obj, dict) else "")
        return obj

    def _run(self, capability: str, kind: str, operation_id: str, fn: Callable[[str], Any],
             action: str | None = None) -> Any:
        idem = self._opts(operation_id, kind)["idempotency_key"]
        return self._call(capability, lambda: fn(idem), action=action or kind,
                          op=operation_id, idem=idem)

    @staticmethod
    def _opts(operation_id: str, kind: str) -> dict:
        return {"idempotency_key": f"treasury-{kind}-{operation_id}"}

    @staticmethod
    def _base(obj: Any, op: str, amount: int = 0, currency: str = DEFAULT_CURRENCY) -> dict:
        return {"provider_ref": obj["id"], "mode": "stripe_test", "livemode": False,
                "status": str(obj.get("status") or ""),
                "amount_minor": int(obj.get("amount") or amount),
                "currency": str(obj.get("currency") or currency).lower(), "operation_id": op}

    # -- operations -------------------------------------------------------
    def create_payment(self, operation_id, amount_minor, currency, payment_method="pm_card_visa",
                       transfer_group=None):
        require_positive_minor(amount_minor)
        params: dict = {"amount": amount_minor, "currency": currency.lower(),
                        "payment_method": payment_method, "confirm": True,
                        "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"},
                        "metadata": {"operation_id": operation_id}}
        if transfer_group:
            params["transfer_group"] = transfer_group
        pi = self._run("payments", "payment", operation_id,
                       lambda idem: self.client.payment_intents.create(
                           params=params, options={"idempotency_key": idem}))
        charge = pi.get("latest_charge")
        charge = charge.get("id") if isinstance(charge, dict) else (charge or "")
        return PaymentResult(**self._base(pi, operation_id, amount_minor, currency),
                             charge_ref=str(charge), transfer_group=transfer_group or "")

    def refund(self, operation_id, payment_ref, amount_minor=None):
        if amount_minor is not None:
            require_positive_minor(amount_minor)
        params: dict = {"payment_intent": payment_ref,
                        "metadata": {"operation_id": operation_id}}
        if amount_minor:
            params["amount"] = amount_minor
        r = self._run("payments", "refund", operation_id, lambda idem: self.client.refunds.create(
            params=params, options={"idempotency_key": idem}))
        return RefundResult(**self._base(r, operation_id, amount_minor or 0),
                            payment_ref=payment_ref)

    def transfer(self, operation_id, amount_minor, currency, destination,
                 source_transaction=None, transfer_group=None):
        require_positive_minor(amount_minor)
        params: dict = {"amount": amount_minor, "currency": currency.lower(),
                        "destination": destination, "metadata": {"operation_id": operation_id}}
        if source_transaction:
            params["source_transaction"] = source_transaction
        if transfer_group:
            params["transfer_group"] = transfer_group
        t = self._run("connect_transfers", "transfer", operation_id,
                      lambda idem: self.client.transfers.create(
                          params=params, options={"idempotency_key": idem}))
        base = self._base(t, operation_id, amount_minor, currency)
        base["status"] = "reversed" if t.get("reversed") else "paid"
        return TransferResult(**base, destination=destination,
                              source_transaction=source_transaction or "",
                              transfer_group=str(t.get("transfer_group") or transfer_group or ""))

    def issue_card(self, operation_id, cardholder_name, currency=DEFAULT_CURRENCY):
        parts = cardholder_name.split() or ["Trip", "Driver"]
        ch = self._run("issuing", "cardholder", operation_id,
                       lambda idem: self.client.issuing.cardholders.create(
            params={"name": cardholder_name, "type": "individual", "status": "active",
                    "email": "agent@example.com", "phone_number": "+18008675309",
                    "individual": {"first_name": parts[0], "last_name": parts[-1] if len(parts) > 1 else "Agent",
                                   "dob": {"day": 1, "month": 1, "year": 1990},
                                   "card_issuing": {"user_terms_acceptance": {
                                       "date": int(datetime.now(UTC).timestamp()),
                                       "ip": "127.0.0.1"}}},
                    "billing": {"address": {"line1": "354 Oyster Point Blvd", "city": "South San Francisco",
                                            "state": "CA", "postal_code": "94080", "country": "US"}}},
            options={"idempotency_key": idem}), action="issuing.cardholder.create")
        card = self._run("issuing", "card", operation_id,
                         lambda idem: self.client.issuing.cards.create(
            params={"cardholder": ch["id"], "currency": currency.lower(), "type": "virtual",
                    "status": "active", "metadata": {"operation_id": operation_id}},
            options={"idempotency_key": idem}), action="issuing.card.create")
        base = self._base(card, operation_id, 0, currency)
        return CardResult(**base, last4=str(card.get("last4") or ""), cardholder_ref=ch["id"])

    def authorize_fuel(self, operation_id, card_ref, amount_minor, currency=DEFAULT_CURRENCY):
        require_positive_minor(amount_minor)
        a = self._run("issuing", "auth", operation_id,
                      lambda idem: self.client.test_helpers.issuing.authorizations.create(
            params={"card": card_ref, "amount": amount_minor, "currency": currency.lower(),
                    "merchant_data": {"category": "service_stations", "name": "Test Fuel Stop",
                                      "country": "GB"}},
            options={"idempotency_key": idem}), action="issuing.authorization.create")
        base = self._base(a, operation_id, amount_minor, currency)
        return FuelAuthResult(**base, card_ref=card_ref, approved=bool(a.get("approved")))

    def capture_fuel(self, operation_id, authorization_ref, amount_minor=None):
        if amount_minor is not None:
            require_positive_minor(amount_minor)
        params = {"capture_amount": amount_minor} if amount_minor else {}
        a = self._run("issuing", "capture", operation_id,
                      lambda idem: self.client.test_helpers.issuing.authorizations.capture(
            authorization_ref, params=params, options={"idempotency_key": idem}),
            action="issuing.authorization.capture")
        base = self._base(a, operation_id, amount_minor or 0)
        base["status"] = "captured" if a.get("status") == "closed" else str(a.get("status"))
        return FuelCaptureResult(**base, authorization_ref=authorization_ref)

    def release_authorization(self, operation_id, authorization_ref):
        """Reverse an uncaptured test authorization; fall back to expiring it."""
        helpers = self.client.test_helpers.issuing.authorizations
        try:
            a = self._run("issuing", "release", operation_id,
                          lambda idem: helpers.reverse(authorization_ref, params={},
                                                       options={"idempotency_key": idem}),
                          action="issuing.authorization.reverse")
        except CapabilityUnavailable:
            raise
        except GatewayError as first:
            if isinstance(first, OutcomeUnknown):
                raise
            a = self._run("issuing", "release-expire", operation_id,
                          lambda idem: helpers.expire(authorization_ref, params={},
                                                      options={"idempotency_key": idem}),
                          action="issuing.authorization.expire")
        base = self._base(a, operation_id, 0)
        base["status"] = "reversed" if a.get("status") in ("reversed", "closed", "expired") \
            else str(a.get("status"))
        return FuelReleaseResult(**base, authorization_ref=authorization_ref)

    def create_invoice(self, operation_id, amount_minor, currency, customer_email,
                       description="", payment_method="pm_card_visa"):
        require_positive_minor(amount_minor)
        cur = currency.lower()

        def step(kind, fn, action):
            return self._run("invoicing", f"{kind}", operation_id, fn, action=action)

        cust = step("customer", lambda idem: self.client.customers.create(
            params={"email": customer_email, "payment_method": payment_method,
                    "invoice_settings": {"default_payment_method": payment_method}},
            options={"idempotency_key": idem}), "customers.create")
        step("item", lambda idem: self.client.invoice_items.create(
            params={"customer": cust["id"], "amount": amount_minor, "currency": cur,
                    "description": description or "Treasury invoice"},
            options={"idempotency_key": idem}), "invoiceitems.create")
        inv = step("invoice", lambda idem: self.client.invoices.create(
            params={"customer": cust["id"], "collection_method": "charge_automatically",
                    "pending_invoice_items_behavior": "include", "currency": cur,
                    "metadata": {"operation_id": operation_id}},
            options={"idempotency_key": idem}), "invoices.create")
        inv = step("finalize", lambda idem: self.client.invoices.finalize_invoice(
            inv["id"], params={}, options={"idempotency_key": idem}), "invoices.finalize")
        inv = step("pay", lambda idem: self.client.invoices.pay(
            inv["id"], params={}, options={"idempotency_key": idem}), "invoices.pay")
        base = self._base(inv, operation_id, amount_minor, currency)
        base["amount_minor"] = int(inv.get("amount_paid") or amount_minor)
        return InvoiceResult(**base, hosted_url=str(inv.get("hosted_invoice_url") or ""))

    # -- sandbox provisioning (used by scripts/bootstrap_sandbox.py) -----------
    def create_connect_account(self, operation_id: str, role: str,
                               country: str = "US") -> dict:
        """Custom account with transfers capability and Stripe's test verification data."""
        now = int(datetime.now(UTC).timestamp())
        acct = self._run("connect_transfers", "account", operation_id,
                         lambda idem: self.client.accounts.create(
            params={"type": "custom", "country": country, "email": f"{role}@example.com",
                    "capabilities": {"transfers": {"requested": True}},
                    "business_type": "individual",
                    "business_profile": {"url": "https://accessible.stripe.com", "mcc": "5734"},
                    "individual": {"first_name": role.capitalize(), "last_name": "Test",
                                   "email": f"{role}@example.com", "phone": "0000000000",
                                   "dob": {"day": 1, "month": 1, "year": 1901},
                                   "ssn_last_4": "0000", "id_number": "000000000",
                                   "address": {"line1": "address_full_match", "city": "San Francisco",
                                               "state": "CA", "postal_code": "94103",
                                               "country": country}},
                    "external_account": {"object": "bank_account", "country": country,
                                         "currency": DEFAULT_CURRENCY, "routing_number": "110000000",
                                         "account_number": "000123456789"},
                    "tos_acceptance": {"date": now, "ip": "127.0.0.1"},
                    "metadata": {"treasury_role": role}},
            options={"idempotency_key": idem}), action="accounts.create")
        return acct

    def retrieve_account(self, account_id: str) -> dict:
        return self._call("connect_transfers",
                          lambda: self.client.accounts.retrieve(account_id),
                          action="accounts.retrieve", op=account_id)

    def topup_issuing(self, operation_id: str, amount_minor: int,
                      currency: str = DEFAULT_CURRENCY) -> dict:
        """Fund the Issuing balance through the Top-ups API (US)."""
        require_positive_minor(amount_minor)
        return self._run("issuing", "topup", operation_id, lambda idem: self.client.topups.create(
            params={"amount": amount_minor, "currency": currency.lower(),
                    "description": "Agent Treasury sandbox Issuing funding",
                    "statement_descriptor": "Treasury test",
                    "destination_balance": "issuing"},
            options={"idempotency_key": idem}), action="topups.create")


def new_run_id() -> str:
    return uuid.uuid4().hex[:8]
