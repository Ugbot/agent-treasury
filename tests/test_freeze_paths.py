"""Freeze / org policy enforced on every payment path (settlement legs, refunds, invoices)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from treasury import routes_freeze, routes_policies
from treasury.gateway import MockGateway
from treasury.geo import Point
from treasury.invoices import InvoiceService, MockInvoiceProvider, PaymentsFrozen
from treasury.ledger import Ledger
from treasury.policies import default_registry
from treasury.proof import SettlementService, seed_order
from treasury.refunds import Order, RefundService

APPR = {"X-Actor-Role": "approver", "X-Actor-Id": "ann"}


@pytest.fixture
def env(tmp_path):
    led = Ledger(tmp_path / "t.sqlite")
    reg = default_registry()
    routes_policies.set_registry(reg)
    app = FastAPI()
    app.state.audit = led.audit
    app.include_router(routes_freeze.router)
    app.include_router(routes_policies.router)
    c = TestClient(app)
    yield c, reg, led.audit
    routes_policies.set_registry(None)


def freeze(c, active):
    r = c.post("/freeze", json={"active": active, "reason": "t"}, headers=APPR)
    assert r.status_code == 200
    return r.json()["data"]


def test_freeze_state_reads_attached_version_and_audit_party(env):
    c, _reg, audit = env
    assert freeze(c, True)["active"] is True
    assert c.get("/freeze").json()["data"]["active"] is True
    row = audit.query(kind="policy.freeze", limit=10)[-1]
    assert row["mission_id"] == "org" and row["party"] == "org"


def test_settlement_legs_blocked_then_retry_after_unfreeze(env):
    c, reg, audit = env
    freeze(c, True)
    gw = MockGateway()
    svc = SettlementService(gw, audit=audit, registry=reg)
    o = svc.add_order(seed_order())
    svc.fund_order(o.id)
    r = svc.release(o.id, driver_id="drv_1", position=Point(51.5003, -0.12), position_ts=90,
                    recipient_id="rcp_1", nonce=svc.issue_nonce(o.id, 100.0), now=100.0)
    assert not [x for x in gw.calls if x[0] == "transfer"]
    for leg in r["legs"].values():
        assert leg["status"] == "failed" and "FREEZE-1" in leg["error"]
    assert any("FREEZE-1" in e["summary"] for e in audit.query(kind="delivery.leg", limit=50))
    freeze(c, False)
    r2 = svc.retry(o.id)
    assert r2["status"] == "released"


def test_refund_held_while_frozen(env):
    c, reg, audit = env
    freeze(c, True)
    s = RefundService(MockGateway(), audit=audit, registry=reg)
    o = Order("o1", "pi_1", 10000, status="canceled")
    d = s.decide(o)
    assert d["verdict"] == "hold" and d["reason"] == "payments are frozen"
    freeze(c, False)
    assert s.decide(o)["verdict"] == "allow"


def test_invoice_sandbox_pay_blocked_while_frozen(env):
    c, reg, audit = env
    freeze(c, True)
    svc = InvoiceService(MockInvoiceProvider(), registry=reg, audit=audit)
    svc.ensure_invoice("o", 15000, "USD", "a@b.c", 10000)
    with pytest.raises(PaymentsFrozen):
        svc.sandbox_pay("o")
    assert audit.query(kind="invoice.pay", limit=5)
    freeze(c, False)
    assert svc.sandbox_pay("o").funded


@pytest.mark.parametrize("pid", ["org-freeze", "org-base"])
def test_unauthenticated_publish_of_mandatory_policy_forbidden(env, pid):
    c, _reg, _ = env
    freeze(c, False)
    cl = [{"id": "X-1", "prose": "x", "kind": "payment_freeze", "params": {"active": False}}]
    r = c.post(f"/policies/{pid}/versions", json={"title": "t", "clauses": cl, "expected_version": 1})
    assert r.status_code == 403 and "approver" in r.json()["detail"]
    r = c.post("/policies/attachments", json={"target_type": "organisation", "target_id": "org",
               "policy_id": pid, "policy_version": 1, "expected_version": 0})
    assert r.status_code == 403
