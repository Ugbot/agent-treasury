import hashlib
import hmac
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from treasury import routes_invoices
from treasury.gateway import CapabilityUnavailable, LiveModeError
from treasury.invoices import (
    CollectionConflict,
    InvoiceService,
    MockInvoiceProvider,
    NotFunded,
    SignatureError,
    StripeInvoiceProvider,
    should_invoice,
)

SECRET = "whsec_testsecret"


def mk(secret=SECRET):
    prov = MockInvoiceProvider()
    return InvoiceService(prov, webhook_secret=secret), prov


def sign(payload: bytes, secret=SECRET, ts=None):
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def event(eid, etype, inv_id, due=15000, paid=15000, status="paid", pi="pi_1", live=False):
    return {"id": eid, "object": "event", "type": etype, "livemode": live,
            "data": {"object": {"object": "invoice", "id": inv_id, "status": status,
                                "amount_due": due, "amount_paid": paid, "currency": "usd",
                                "payment_intent": pi, "livemode": live}}}


def test_threshold_boundary():
    assert should_invoice(15000, 10000) and not should_invoice(10000, 10000)
    svc, prov = mk()
    assert svc.ensure_invoice("o1", 10000, "USD", "a@b.c", 10000) is None
    assert prov.create_calls == 0
    s = svc.ensure_invoice("o2", 15000, "USD", "a@b.c", 10000)
    assert s and s.status == "open" and s.hosted_url and not s.funded
    assert svc.ensure_invoice("o2", 15000, "USD", "a@b.c", 10000).provider_ref == s.provider_ref
    assert len(prov.invoices) == 1


def test_issued_invoice_never_funded_and_gate_blocks():
    svc, _prov = mk()
    s = svc.ensure_invoice("o", 15000, "USD", "a@b.c", 10000)
    assert s.phase == "awaiting_payment" and not svc.is_funded("o")
    with pytest.raises(NotFunded):
        svc.require_funded("o")
    with pytest.raises(NotFunded):
        svc.require_funded("missing")


def test_paid_webhook_unlocks_idempotent():
    svc, prov = mk()
    s = svc.ensure_invoice("o", 15000, "USD", "a@b.c", 10000)
    prov.mark_paid(s.provider_ref)
    body = json.dumps(event("evt_1", "invoice.paid", s.provider_ref)).encode()
    r = svc.handle_webhook(body, sign(body))
    assert r["applied"] and svc.require_funded("o").phase == "confirmed"
    assert svc.get("o").payment_ref
    r2 = svc.handle_webhook(body, sign(body))
    assert r2["duplicate"] and not r2["applied"]


def test_signed_paid_event_without_provider_payment_does_not_fund():
    svc, _prov = mk()
    s = svc.ensure_invoice("o", 15000, "USD", "a@b.c", 10000)
    body = json.dumps(event("evt_x", "invoice.paid", s.provider_ref)).encode()
    svc.handle_webhook(body, sign(body))
    assert not svc.is_funded("o")  # provider still says open


def test_event_without_reconcile_needs_evidence_and_full_amount():
    svc, _ = mk()
    s = svc.ensure_invoice("o", 15000, "USD", "a@b.c", 10000)
    svc.handle_event(event("e1", "invoice.paid", s.provider_ref, paid=5000), reconcile=False)
    assert not svc.is_funded("o")
    svc.handle_event(event("e2", "invoice.paid", s.provider_ref, pi=""), reconcile=False)
    assert not svc.is_funded("o")  # paid but no payment evidence
    svc.handle_event(event("e3", "invoice.paid", s.provider_ref), reconcile=False)
    assert svc.is_funded("o")


def test_failed_and_stale_events():
    svc, prov = mk()
    s = svc.ensure_invoice("o", 15000, "USD", "a@b.c", 10000)
    svc.handle_event(event("f1", "invoice.payment_failed", s.provider_ref, paid=0,
                           status="open", pi=""))
    assert not svc.is_funded("o")
    prov.mark_paid(s.provider_ref)
    svc.handle_event(event("p1", "invoice.paid", s.provider_ref))
    assert svc.is_funded("o")
    svc.handle_event(event("f2", "invoice.payment_failed", s.provider_ref, paid=0,
                           status="open", pi=""))
    assert svc.is_funded("o")  # late failure cannot un-fund
    svc2, _ = mk()
    s2 = svc2.ensure_invoice("v", 15000, "USD", "a@b.c", 10000)
    svc2.handle_event(event("v1", "invoice.voided", s2.provider_ref, status="void", paid=0))
    assert svc2.get("v").phase == "failed"


def test_bad_signature_and_missing_secret_and_livemode():
    svc, _ = mk()
    body = b'{"id":"evt","type":"invoice.paid"}'
    with pytest.raises(SignatureError):
        svc.handle_webhook(body, sign(body, secret="whsec_wrong"))
    with pytest.raises(SignatureError):
        svc.handle_webhook(body, "garbage")
    with pytest.raises(CapabilityUnavailable) as ei:
        mk(secret=None)[0].handle_webhook(body, sign(body))
    assert "STRIPE_WEBHOOK_SECRET" in ei.value.setup
    with pytest.raises(LiveModeError):
        svc.handle_event(event("L", "invoice.paid", "in_x", live=True))


def test_mutual_exclusion_and_no_rebill_after_funded():
    svc, prov = mk()
    svc.claim_collection("a", "payment_intent", 15000, "usd")
    with pytest.raises(CollectionConflict):
        svc.ensure_invoice("a", 15000, "USD", "a@b.c", 10000)
    s = svc.ensure_invoice("b", 15000, "USD", "a@b.c", 10000)
    with pytest.raises(CollectionConflict):
        svc.claim_collection("b", "payment_intent", 15000, "usd")
    prov.mark_paid(s.provider_ref)
    svc.poll("b")
    # chain edited: amount changed, funded obligation is not billed again
    again = svc.ensure_invoice("b", 20000, "USD", "a@b.c", 10000)
    assert again.funded and again.amount_minor == 15000 and len(prov.invoices) == 1
    with pytest.raises(CollectionConflict):
        svc.void_unpaid("b")


def test_unpaid_amount_change_conflicts():
    svc, _ = mk()
    svc.ensure_invoice("o", 15000, "USD", "a@b.c", 10000)
    with pytest.raises(CollectionConflict):
        svc.ensure_invoice("o", 16000, "USD", "a@b.c", 10000)


def test_poll_and_sandbox_pay_and_partial():
    svc, prov = mk()
    s = svc.ensure_invoice("o", 15000, "USD", "a@b.c", 10000)
    prov.mark_paid(s.provider_ref, amount_paid=5000)
    assert not svc.poll("o").funded
    assert svc.sandbox_pay("o").funded


def test_persistence_across_restart(tmp_path):
    db = str(tmp_path / "i.sqlite")
    prov = MockInvoiceProvider()
    a = InvoiceService(prov, db)
    s = a.ensure_invoice("o", 15000, "USD", "a@b.c", 10000)
    prov.mark_paid(s.provider_ref)
    a.poll("o")
    b = InvoiceService(prov, db)
    assert b.is_funded("o")
    assert b.ensure_invoice("o", 15000, "USD", "a@b.c", 10000).provider_ref == s.provider_ref


def test_stripe_provider_rejects_live_and_missing_key(monkeypatch):
    with pytest.raises(LiveModeError):
        StripeInvoiceProvider(api_key="sk_live_abc")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    with pytest.raises(CapabilityUnavailable):
        StripeInvoiceProvider()


class FakeStripe:
    """Minimal fake StripeClient proving send_invoice (never auto-pay) is used."""
    def __init__(self):
        self.calls = []
        outer = self

        class NS:
            def __init__(s, name): s.name = name
            def __getattr__(s, m):
                def f(*a, params=None, options=None):
                    outer.calls.append((s.name, m, params))
                    if (s.name, m) == ("invoices", "create"):
                        return {"id": "in_1", "status": "draft"}
                    if (s.name, m) == ("invoices", "finalize_invoice"):
                        return {"id": "in_1", "status": "open", "amount_due": 15000,
                                "amount_paid": 0, "currency": "usd",
                                "hosted_invoice_url": "https://x/in_1", "livemode": False}
                    return {"id": f"{s.name}_1"}
                return f
        self.customers, self.invoices, self.invoice_items = NS("customers"), NS("invoices"), NS("invoice_items")


def test_stripe_provider_creates_open_invoice_without_paying():
    fake = FakeStripe()
    prov = StripeInvoiceProvider(client=fake)
    pi = prov.create_invoice("op", 15000, "USD", "a@b.c", "d")
    assert pi.status == "open" and pi.hosted_url
    names = [(n, m) for n, m, _ in fake.calls]
    assert ("invoices", "pay") not in names
    create = next(p for n, m, p in fake.calls if (n, m) == ("invoices", "create"))
    assert create["collection_method"] == "send_invoice"


def test_routes():
    svc, prov = mk()
    routes_invoices.set_service(svc)
    app = FastAPI()
    app.include_router(routes_invoices.router)
    c = TestClient(app)
    body = {"obligation_id": "o", "amount_minor": 15000, "customer_email": "a@b.c",
            "threshold_minor": 10000}
    assert c.post("/invoices", json={**body, "amount_minor": 10000}).json()["invoiced"] is False
    r = c.post("/invoices", json=body).json()
    assert r["invoiced"] and r["phase"] == "awaiting_payment"
    assert c.get("/invoices/o/gate").status_code == 402
    prov.mark_paid(r["provider_ref"])
    raw = json.dumps(event("evt_r", "invoice.paid", r["provider_ref"])).encode()
    assert c.post("/invoices/webhooks/stripe", content=raw,
                  headers={"stripe-signature": "bad"}).status_code == 400
    ok = c.post("/invoices/webhooks/stripe", content=raw,
                headers={"stripe-signature": sign(raw)})
    assert ok.status_code == 200
    assert c.get("/invoices/o/gate").json()["funded"] is True
    assert c.post("/invoices/o/poll").json()["phase"] == "confirmed"
    routes_invoices.set_service(InvoiceService(prov, webhook_secret=None))
    assert c.post("/invoices/webhooks/stripe", content=raw).status_code == 503
    routes_invoices.set_service(None)
