"""Invoice-above-threshold collection that waits for ACTUAL payment.

Rules enforced here:
- An invoice is only ever *created and finalized* (send_invoice, never auto-paid).
  Creation, a hosted URL, or an "open" status never satisfies funded.
- Funded requires: status == "paid", amount_paid >= expected amount, matching currency
  and provider payment evidence (payment intent / charge reference).
- Invoice collection and PaymentIntent collection are mutually exclusive per obligation.
- Webhook events are signature verified, test-mode only, and deduplicated by event id.
- Polling the provider is the sandbox fallback for a missed or uncertain webhook.
- Money is integer minor units. Stripe TEST mode only.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .gateway import (
    CapabilityUnavailable,
    GatewayError,
    LiveModeError,
    require_positive_minor,
)
from .stripe_gateway import SETUP, check_livemode, redact, validate_test_key

WEBHOOK_SETUP = ("Set STRIPE_WEBHOOK_SECRET to the whsec_... signing secret printed by "
                 "`stripe listen --forward-to localhost:8000/invoices/webhooks/stripe` "
                 "(or a sandbox webhook endpoint). Use POST /invoices/{obligation_id}/poll "
                 "as the manual fallback.")

PAID_EVENTS = {"invoice.paid", "invoice.payment_succeeded"}
FAILED_EVENTS = {"invoice.payment_failed"}
VOID_EVENTS = {"invoice.voided", "invoice.marked_uncollectible"}
HANDLED_EVENTS = PAID_EVENTS | FAILED_EVENTS | VOID_EVENTS | {
    "invoice.finalized", "invoice.created", "invoice.updated", "invoice.sent"}


class InvoiceError(Exception):
    pass


class CollectionConflict(InvoiceError):
    """Obligation is already collected by a different method or with a different amount."""


class NotFunded(InvoiceError):
    """Raised by the release gate while payment is not confirmed."""


class SignatureError(InvoiceError):
    pass


def should_invoice(amount_minor: int, threshold_minor: int) -> bool:
    """Strictly greater-than: exactly the threshold follows the ordinary collection path."""
    require_positive_minor(amount_minor)
    return amount_minor > threshold_minor


# ---------------------------------------------------------------- providers

@dataclass(frozen=True)
class ProviderInvoice:
    """Provider view of an invoice; ``raw`` is the (redacted) Stripe-like object."""
    id: str
    status: str            # draft | open | paid | void | uncollectible
    amount_due: int
    amount_paid: int
    currency: str
    hosted_url: str = ""
    payment_ref: str = ""  # payment intent or charge id, "" when none
    livemode: bool = False


class InvoiceProvider(Protocol):
    mode: str

    def create_invoice(self, operation_id: str, amount_minor: int, currency: str,
                       customer_email: str, description: str) -> ProviderInvoice: ...

    def retrieve_invoice(self, invoice_id: str) -> ProviderInvoice: ...

    def sandbox_pay(self, invoice_id: str) -> None: ...


def invoice_from_object(obj: dict) -> ProviderInvoice:
    """Normalize a Stripe invoice object (dict-like). Rejects livemode=true."""
    check_livemode(obj)
    ref = obj.get("payment_intent") or obj.get("charge") or ""
    if not ref:
        # newer API versions expose payments[].payment.payment_intent
        payments = (obj.get("payments") or {}).get("data") or []
        for p in payments:
            pay = p.get("payment") or {}
            ref = pay.get("payment_intent") or pay.get("charge") or ""
            if ref:
                break
    if isinstance(ref, dict):
        ref = ref.get("id", "")
    return ProviderInvoice(
        id=str(obj.get("id") or ""), status=str(obj.get("status") or ""),
        amount_due=int(obj.get("amount_due") or 0), amount_paid=int(obj.get("amount_paid") or 0),
        currency=str(obj.get("currency") or "").lower(),
        hosted_url=str(obj.get("hosted_invoice_url") or ""), payment_ref=str(ref or ""),
        livemode=False)


class MockInvoiceProvider:
    """Deterministic in-memory provider. Invoices stay open until ``mark_paid`` (the
    stand-in for the customer paying); creation never pays."""
    mode = "mock"

    def __init__(self) -> None:
        self.invoices: dict[str, ProviderInvoice] = {}
        self._by_op: dict[str, str] = {}
        self.create_calls = 0

    def create_invoice(self, operation_id, amount_minor, currency, customer_email, description=""):
        require_positive_minor(amount_minor)
        self.create_calls += 1
        if operation_id in self._by_op:
            return self.invoices[self._by_op[operation_id]]
        iid = f"in_mock_{len(self.invoices) + 1:04d}"
        inv = ProviderInvoice(iid, "open", amount_minor, 0, currency.lower(),
                              f"https://mock.invalid/invoice/{iid}")
        self.invoices[iid] = inv
        self._by_op[operation_id] = iid
        return inv

    def retrieve_invoice(self, invoice_id):
        if invoice_id not in self.invoices:
            raise GatewayError(f"unknown invoice {invoice_id}")
        return self.invoices[invoice_id]

    def mark_paid(self, invoice_id: str, amount_paid: int | None = None, ref: str | None = None):
        i = self.invoices[invoice_id]
        paid = i.amount_due if amount_paid is None else amount_paid
        full = paid >= i.amount_due
        self.invoices[invoice_id] = ProviderInvoice(
            i.id, "paid" if full else "open", i.amount_due, paid, i.currency, i.hosted_url,
            (f"pi_mock_{invoice_id}" if ref is None else ref) if paid else "")
        return self.invoices[invoice_id]

    def sandbox_pay(self, invoice_id):
        self.mark_paid(invoice_id)


class StripeInvoiceProvider:
    """Stripe TEST-mode invoices. Uses send_invoice so creation never pays."""
    mode = "stripe_test"

    def __init__(self, api_key: str | None = None, client: Any = None):
        if client is None:
            import os
            key = validate_test_key(api_key if api_key is not None
                                    else os.environ.get("STRIPE_SECRET_KEY"))
            import stripe
            client = stripe.StripeClient(key)
        elif api_key is not None:
            validate_test_key(api_key)
        self.client = client

    def _call(self, fn):
        import stripe
        try:
            obj = fn()
        except stripe.AuthenticationError as e:
            raise CapabilityUnavailable("invoicing", "Stripe rejected the API key",
                                        SETUP["payments"]) from e
        except stripe.PermissionError as e:
            raise CapabilityUnavailable("invoicing", str(redact(str(e)))[:200],
                                        SETUP["invoicing"]) from e
        except stripe.StripeError as e:
            raise GatewayError(str(redact(str(e)))[:300]) from e
        check_livemode(obj)
        return obj

    def create_invoice(self, operation_id, amount_minor, currency, customer_email,
                       description=""):
        require_positive_minor(amount_minor)
        cur = currency.lower()

        def o(k):
            return {"idempotency_key": f"treasury-inv-{k}-{operation_id}"}
        cust = self._call(lambda: self.client.customers.create(
            params={"email": customer_email, "metadata": {"operation_id": operation_id}},
            options=o("customer")))
        inv = self._call(lambda: self.client.invoices.create(
            params={"customer": cust["id"], "collection_method": "send_invoice",
                    "days_until_due": 7, "currency": cur,
                    "pending_invoice_items_behavior": "exclude",
                    "metadata": {"operation_id": operation_id}},
            options=o("invoice")))
        self._call(lambda: self.client.invoice_items.create(
            params={"customer": cust["id"], "invoice": inv["id"], "amount": amount_minor,
                    "currency": cur, "description": description or "Treasury invoice"},
            options=o("item")))
        inv = self._call(lambda: self.client.invoices.finalize_invoice(
            inv["id"], params={}, options=o("finalize")))
        return invoice_from_object(inv)

    def retrieve_invoice(self, invoice_id):
        inv = self._call(lambda: self.client.invoices.retrieve(
            invoice_id, params={"expand": ["payments"]}))
        return invoice_from_object(inv)

    def sandbox_pay(self, invoice_id):
        """TEST-mode helper: ask Stripe to pay the invoice with a test card."""
        self._call(lambda: self.client.invoices.pay(
            invoice_id, params={"payment_method": "pm_card_visa"},
            options={"idempotency_key": f"treasury-inv-sandboxpay-{invoice_id}"}))


# ------------------------------------------------------------------- store

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
  obligation_id TEXT PRIMARY KEY, method TEXT NOT NULL,
  amount_minor INTEGER NOT NULL, currency TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS invoices (
  obligation_id TEXT PRIMARY KEY, provider_ref TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL, amount_minor INTEGER NOT NULL, amount_paid_minor INTEGER NOT NULL,
  currency TEXT NOT NULL, hosted_url TEXT NOT NULL, payment_ref TEXT NOT NULL,
  mode TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS invoice_events (
  event_id TEXT PRIMARY KEY, type TEXT NOT NULL, received_at REAL NOT NULL);
"""


@dataclass(frozen=True)
class InvoiceState:
    obligation_id: str
    provider_ref: str
    status: str            # provider status: open | paid | void | uncollectible | draft
    amount_minor: int
    amount_paid_minor: int
    currency: str
    hosted_url: str
    payment_ref: str
    mode: str
    phase: str             # issued | awaiting_payment | confirmed | failed
    funded: bool

    def public(self) -> dict:
        return dict(self.__dict__)


def _phase(status: str, expected: int, paid: int, currency_ok: bool, ref: str) -> tuple[str, bool]:
    if status == "paid" and paid >= expected and currency_ok and ref:
        return "confirmed", True
    if status in ("void", "uncollectible"):
        return "failed", False
    if status == "draft":
        return "issued", False
    return "awaiting_payment", False


@dataclass
class InvoiceService:
    provider: InvoiceProvider
    db_path: str = ":memory:"
    webhook_secret: str | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _db: sqlite3.Connection = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    # -- collection exclusivity -------------------------------------------
    def claim_collection(self, obligation_id: str, method: str, amount_minor: int,
                         currency: str) -> None:
        """Record how an obligation is collected. 'invoice' and 'payment_intent' are
        mutually exclusive; re-claiming the same method is a no-op."""
        if method not in ("invoice", "payment_intent"):
            raise ValueError("method must be 'invoice' or 'payment_intent'")
        with self._lock:
            row = self._db.execute("SELECT * FROM collections WHERE obligation_id=?",
                                   (obligation_id,)).fetchone()
            if row:
                if row["method"] != method:
                    raise CollectionConflict(
                        f"obligation {obligation_id} is already collected by {row['method']}")
                return
            self._db.execute("INSERT INTO collections VALUES (?,?,?,?)",
                             (obligation_id, method, amount_minor, currency.lower()))
            self._db.commit()

    # -- queries ----------------------------------------------------------
    def _row_state(self, row: sqlite3.Row) -> InvoiceState:
        phase, funded = _phase(row["status"], row["amount_minor"], row["amount_paid_minor"],
                               True, row["payment_ref"])
        return InvoiceState(row["obligation_id"], row["provider_ref"], row["status"],
                            row["amount_minor"], row["amount_paid_minor"], row["currency"],
                            row["hosted_url"], row["payment_ref"], row["mode"], phase, funded)

    def get(self, obligation_id: str) -> InvoiceState | None:
        row = self._db.execute("SELECT * FROM invoices WHERE obligation_id=?",
                               (obligation_id,)).fetchone()
        return self._row_state(row) if row else None

    def is_funded(self, obligation_id: str) -> bool:
        s = self.get(obligation_id)
        return bool(s and s.funded)

    def require_funded(self, obligation_id: str) -> InvoiceState:
        """Release gate for downstream disbursement."""
        s = self.get(obligation_id)
        if s is None:
            raise NotFunded(f"no invoice for obligation {obligation_id}")
        if not s.funded:
            raise NotFunded(f"invoice {s.provider_ref} is {s.phase}, not confirmed paid")
        return s

    # -- creation ---------------------------------------------------------
    def ensure_invoice(self, obligation_id: str, amount_minor: int, currency: str,
                       customer_email: str, threshold_minor: int,
                       description: str = "") -> InvoiceState | None:
        """Create at most one invoice for the obligation when amount > threshold.

        Returns None when the amount does not exceed the threshold (ordinary collection
        path applies). Repeat calls and chain edits never bill twice: an existing
        invoice is returned untouched, and a funded one is never replaced.
        """
        require_positive_minor(amount_minor)
        if not should_invoice(amount_minor, threshold_minor):
            return None
        cur = currency.lower()
        with self._lock:
            existing = self.get(obligation_id)
            if existing:
                if existing.amount_minor != amount_minor and not existing.funded:
                    raise CollectionConflict(
                        "an unpaid invoice with a different amount exists; void it first")
                return existing  # funded obligations are never re-billed
            self.claim_collection(obligation_id, "invoice", amount_minor, cur)
            op = f"invoice:{obligation_id}"
            pi = self.provider.create_invoice(op, amount_minor, cur, customer_email, description)
            if pi.livemode:
                raise LiveModeError("provider returned livemode=true")
            # Even if the provider returned something paid, only verified state counts;
            # store what the provider says and let the phase rules decide.
            self._db.execute(
                "INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?,?)",
                (obligation_id, pi.id, pi.status, amount_minor, pi.amount_paid, cur,
                 pi.hosted_url, pi.payment_ref, self.provider.mode, time.time()))
            self._db.commit()
            return self.get(obligation_id)

    def void_unpaid(self, obligation_id: str) -> None:
        """Local release of an unpaid, failed invoice so a corrected one can be issued."""
        with self._lock:
            s = self.get(obligation_id)
            if s is None:
                return
            if s.funded or s.status == "paid":
                raise CollectionConflict("a paid invoice cannot be discarded")
            self._db.execute("DELETE FROM invoices WHERE obligation_id=?", (obligation_id,))
            self._db.execute("DELETE FROM collections WHERE obligation_id=?", (obligation_id,))
            self._db.commit()

    # -- applying provider state ------------------------------------------
    def _apply(self, provider_ref: str, p: ProviderInvoice, event_type: str = "") -> InvoiceState | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM invoices WHERE provider_ref=?",
                                   (provider_ref,)).fetchone()
            if row is None:
                return None
            if row["status"] == "paid" and p.status != "paid":
                return self._row_state(row)          # paid is sticky vs stale/out-of-order
            if p.currency and p.currency != row["currency"]:
                return self._row_state(row)          # wrong currency can never fund
            status = p.status or row["status"]
            paid = p.amount_paid
            if status == "paid" and (paid < row["amount_minor"]
                                     or p.amount_due != row["amount_minor"]):
                status = "open"                      # partial / mismatched: not funded
            if event_type in FAILED_EVENTS and status == "paid":
                status = "open"
            self._db.execute(
                "UPDATE invoices SET status=?, amount_paid_minor=?, payment_ref=?, "
                "hosted_url=COALESCE(NULLIF(?,''),hosted_url), updated_at=? WHERE provider_ref=?",
                (status, paid, p.payment_ref, p.hosted_url,
                 time.time(), provider_ref))
            self._db.commit()
            return self.get(row["obligation_id"])

    def poll(self, obligation_id: str) -> InvoiceState:
        """Manual/sandbox fallback: reconcile against the provider's current state."""
        s = self.get(obligation_id)
        if s is None:
            raise InvoiceError(f"no invoice for obligation {obligation_id}")
        out = self._apply(s.provider_ref, self.provider.retrieve_invoice(s.provider_ref))
        return out or s

    def sandbox_pay(self, obligation_id: str) -> InvoiceState:
        """TEST helper: have the provider pay, then wait on verified provider state."""
        s = self.get(obligation_id)
        if s is None:
            raise InvoiceError(f"no invoice for obligation {obligation_id}")
        if s.mode == "stripe_test" or s.mode == "mock":
            self.provider.sandbox_pay(s.provider_ref)
        return self.poll(obligation_id)

    # -- webhooks ---------------------------------------------------------
    def verify_event(self, payload: bytes, signature_header: str, tolerance: int = 300) -> dict:
        """Verify the Stripe-Signature header and return the event dict."""
        if not self.webhook_secret:
            raise CapabilityUnavailable("webhooks", "STRIPE_WEBHOOK_SECRET is not set",
                                        WEBHOOK_SETUP)
        import stripe
        try:
            ev = stripe.Webhook.construct_event(payload, signature_header,
                                                self.webhook_secret, tolerance)
        except (stripe.SignatureVerificationError, ValueError) as e:
            raise SignatureError("invalid Stripe signature or payload") from e
        return json.loads(json.dumps(ev if isinstance(ev, dict) else ev.to_dict()))

    def handle_event(self, event: dict, reconcile: bool = True) -> dict:
        """Apply an already-verified event. Idempotent by event id."""
        check_livemode(event)
        if event.get("livemode") is True:
            raise LiveModeError("livemode=true events are rejected")
        eid, etype = str(event.get("id") or ""), str(event.get("type") or "")
        if not eid:
            raise InvoiceError("event has no id")
        with self._lock:
            if self._db.execute("SELECT 1 FROM invoice_events WHERE event_id=?",
                                (eid,)).fetchone():
                return {"event_id": eid, "duplicate": True, "applied": False}
            applied, state = False, None
            obj = (event.get("data") or {}).get("object") or {}
            if etype in HANDLED_EVENTS and obj.get("object", "invoice") == "invoice":
                p = invoice_from_object(obj)
                if etype in VOID_EVENTS and p.status not in ("void", "uncollectible"):
                    p = ProviderInvoice(p.id, "void" if etype == "invoice.voided"
                                        else "uncollectible", p.amount_due, p.amount_paid,
                                        p.currency, p.hosted_url, p.payment_ref)
                if reconcile and etype in PAID_EVENTS:
                    # a signed event is only a hint; current provider state is authoritative
                    row = self._db.execute("SELECT obligation_id FROM invoices WHERE "
                                           "provider_ref=?", (p.id,)).fetchone()
                    state = self.poll(row["obligation_id"]) if row else None
                else:
                    state = self._apply(p.id, p, etype)
                applied = state is not None
            self._db.execute("INSERT INTO invoice_events VALUES (?,?,?)",
                             (eid, etype, time.time()))
            self._db.commit()
        return {"event_id": eid, "duplicate": False, "applied": applied,
                "state": state.public() if state else None}

    def handle_webhook(self, payload: bytes, signature_header: str) -> dict:
        return self.handle_event(self.verify_event(payload, signature_header))
