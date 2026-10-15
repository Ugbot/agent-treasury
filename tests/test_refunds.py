import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from treasury import routes_refunds
from treasury.gateway import (
    CapabilityUnavailable,
    GatewayError,
    MockGateway,
    RefundResult,
)
from treasury.refunds import NoPaymentState, Order, RefundService


def order(**kw):
    base = {"order_id": "o1", "payment_ref": "pi_1", "amount_minor": 10000, "status": "canceled"}
    base.update(kw)
    return Order(**base)


def svc(gw=None, **kw):
    s = RefundService(gw or MockGateway())
    s.register_order(order(**kw))
    return s


def test_canceled_before_pickup_refunds_once():
    gw = MockGateway()
    s = svc(gw, fuel_captured_minor=500, processing_fee_minor=320)
    r = s.request_refund("o1", "c1", {"reason": "no driver"})
    assert r["state"] == "confirmed" and r["amount_minor"] == 10000
    assert r["decision"]["clause"] == "REFUND-1" and r["decision"]["policy_hash"]
    assert r["original_payment_ref"] == "pi_1" and r["provider"]["provider_ref"].startswith("re_")
    assert r["cancel_evidence"] == {"reason": "no driver"}
    # duplicate cancel inputs: one semantic refund
    r2 = s.request_refund("o1", "c2")
    assert r2["duplicate"] and r2["provider"] == r["provider"]
    assert len({(k, o) for k, o in gw.calls if k == "refund"}) == 1 and len(gw.calls) == 1
    assert s.get_order("o1").remaining_refundable_minor == 0
    # allowance / transfers untouched; costs explicit
    assert r["allowance_restored"] is False and r["transfers_reversed"] is False
    assert r["costs"] == {"fuel_captured_minor": 500, "processing_fee_minor": 320}


def test_amount_capped_by_remaining_refundable():
    s = svc(refunded_minor=2500)
    r = s.request_refund("o1", "c1")
    assert r["amount_minor"] == 7500


@pytest.mark.parametrize("kw,reason", [
    ({"status": "delivered"}, "already_delivered"),
    ({"status": "picked_up"}, "after_pickup"),
    ({"custody": "ambiguous"}, "custody_ambiguous"),
    ({"refunded_minor": 10000}, "already_refunded"),
    ({"outcome_uncertain": True}, "uncertain_outcome"),
    ({"status": "funded"}, "not_canceled"),
])
def test_unknown_or_settled_order_holds_refund(kw, reason):
    gw = MockGateway()
    s = svc(gw, **kw)
    r = s.request_refund("o1", "c1")
    assert r["state"] in ("held", "escalated") and r["decision"]["reason"] == reason
    assert gw.calls == []


def _registry_with_recovery(**limit):
    from treasury.policies import default_registry
    from treasury.policy import Clause
    reg = default_registry()
    clauses = [Clause("REC-1", "Recovery refund after transfer", "require_input",
                      {"key": "refund_after_transfer"})]
    if limit:
        clauses.append(Clause("REC-2", "Cap", "max_amount", {"limit_minor": limit["cap"]}))
    reg.publish("recovery", "Recovery refund policy", clauses)
    return reg


def test_refund_after_transfer_needs_registry_recovery_policy():
    gw = MockGateway()
    tr = [{"id": "tr_1", "amount_minor": 7000}]
    s = svc(gw, transfers=tr)
    assert s.request_refund("o1", "c1")["state"] == "denied" and gw.calls == []
    # a bare dict with an id is not a policy
    s = svc(gw, transfers=tr)
    r = s.request_refund("o1", "c1", recovery_policy={"id": "REC-1", "version": 1, "max_refund_minor": 3000})
    assert r["state"] == "denied" and r["decision"]["reason"] == "recovery_policy_registry_unavailable"
    reg = _registry_with_recovery(cap=2000)
    s = RefundService(gw, registry=reg)
    s.register_order(order(transfers=tr))
    r = s.request_refund("o1", "c1", recovery_policy={"id": "nope", "version": 1})
    assert r["decision"]["reason"] == "recovery_policy_not_published" and gw.calls == []
    s = RefundService(gw, registry=reg)
    s.register_order(order(transfers=tr))
    r = s.request_refund("o1", "c1", recovery_policy={"id": "recovery", "version": 9})
    assert r["decision"]["reason"] == "recovery_policy_not_published"
    s = RefundService(gw, registry=reg)
    s.register_order(order(transfers=tr))
    r = s.request_refund("o1", "c1", recovery_policy={"id": "recovery", "version": 1})
    assert r["state"] == "confirmed" and r["amount_minor"] == 2000  # policy cap
    assert r["decision"]["recovery_policy_hash"] and r["transfers_reversed"] is False


def test_recovery_policy_weakening_mandatory_is_rejected():
    from treasury.policy import Clause
    reg = _registry_with_recovery()
    reg.publish("loose", "Loose", [Clause("CURRENCY-1", "any", "allowed_currency",
                                          {"currencies": ["usd", "eur"]})])
    s = RefundService(MockGateway(), registry=reg)
    s.register_order(order(transfers=[{"id": "t", "amount_minor": 7000}]))
    r = s.request_refund("o1", "c1", recovery_policy={"id": "loose", "version": 1})
    assert r["decision"]["reason"] == "recovery_policy_weakens_mandatory_policy"


@pytest.mark.parametrize("cur", ["jpy", "eur"])
def test_refund_foreign_currency_denied_with_clause(cur):
    gw = MockGateway()
    s = svc(gw, currency=cur)
    r = s.request_refund("o1", "c1")
    assert r["state"] == "denied" and r["decision"]["clause"] == "CURRENCY-1" and gw.calls == []


def test_refund_currency_read_live(monkeypatch):
    from treasury import policy as pm
    monkeypatch.setattr(pm, "DEFAULT_POLICY", pm.Policy("org-base", 2, tuple(
        pm.Clause(c.id, c.prose, c.kind, {"currencies": ["eur"]}) if c.kind == "allowed_currency" else c
        for c in pm.DEFAULT_POLICY.clauses)))
    assert svc(MockGateway(), currency="usd").request_refund("o1", "c1")["state"] == "denied"
    s = svc(MockGateway(), currency="eur", payment_ref="pi_2")
    assert s.request_refund("o1", "c1")["state"] == "confirmed"


def _settled(gw, funded=True, legs_confirmed=True):
    from treasury.geo import Point
    from treasury.proof import SettlementService, seed_order
    st = SettlementService(gw)
    st.add_order(seed_order("o1"))
    if funded:
        st.fund_order("o1")
    if legs_confirmed:
        n = st.issue_nonce("o1", 100.0)
        r = st.release("o1", driver_id="drv_1", position=Point(51.5003, -0.12), position_ts=90,
                       recipient_id="rcp_1", nonce=n, now=100.0)
        assert r["status"] == "released"
    return st


def test_settled_order_cannot_be_fully_refunded_from_real_state():
    gw = MockGateway()
    st = _settled(gw)
    s = RefundService(gw, settlements=st)
    # client claims nothing about money: order is built from real funding + transfers
    o = s.order_from_state("o1", status="canceled")
    assert o.amount_minor == 10000 and len(o.transfers) == 2 and o.payment_ref.startswith("pi_")
    r = s.request_refund("o1", "c1")
    assert r["state"] == "denied" and r["decision"]["reason"] == "refund_after_transfer_requires_recovery_policy"
    assert not [c for c in gw.calls if c[0] == "refund"]


def test_refund_amount_after_transfers_capped_to_untransferred():
    gw = MockGateway()
    st = _settled(gw)
    reg = _registry_with_recovery()
    s = RefundService(gw, settlements=st, registry=reg)
    s.order_from_state("o1", status="canceled")
    r = s.request_refund("o1", "c1", recovery_policy={"id": "recovery", "version": 1})
    assert r["state"] == "confirmed" and r["amount_minor"] == 1000  # only retention (10000-9000)


def test_stale_client_order_is_overwritten_by_real_state():
    gw = MockGateway()
    st = _settled(gw, legs_confirmed=False)
    s = RefundService(gw, settlements=st)
    s.register_order(order(order_id="o1", payment_ref="pi_fake", amount_minor=999999))
    r = s.request_refund("o1", "c1")
    assert r["original_payment_ref"] == st.funding["o1"]["payment_ref"] and r["amount_minor"] == 10000


def test_unfunded_order_has_no_refundable_payment():
    gw = MockGateway()
    st = _settled(gw, funded=False, legs_confirmed=False)
    s = RefundService(gw, settlements=st)
    with pytest.raises(NoPaymentState):
        s.order_from_state("o1", status="canceled")
    s.register_order(order(order_id="o1"))
    r = s.request_refund("o1", "c1")
    assert r["state"] == "denied" and r["decision"]["reason"] == "no_confirmed_payment"
    assert gw.calls == []


def test_saga_ledger_payment_is_the_source():
    from treasury.ledger import Ledger
    from treasury.saga import SagaStore
    led = Ledger(":memory:")
    gw = MockGateway()
    store = SagaStore(led)
    from treasury import saga
    sg = saga.MockGateway()
    store.reserve("pay:m1", "m1", "payment", 5000, "usd", "mock")
    store.execute("pay:m1", sg)
    s = RefundService(gw, ledger=led)
    o = s.order_from_state("m1", status="canceled")
    assert o.amount_minor == 5000 and o.payment_ref
    assert s.request_refund("m1", "c1")["amount_minor"] == 5000


def test_audit_has_mission_and_party():
    import sqlite3

    from treasury.audit import AuditLog
    db = sqlite3.connect(":memory:")
    s = RefundService(MockGateway(), audit=AuditLog(db))
    s.register_order(order())
    s.request_refund("o1", "c1")
    rows = db.execute("SELECT mission_id, party FROM audit_log WHERE kind='refund'").fetchall()
    assert rows and all(r[0] == "o1" and r[1] for r in rows)


class FlakyGateway(MockGateway):
    """First refund call applies on the provider but raises (uncertain outcome)."""
    def __init__(self, with_lookup):
        super().__init__()
        self.fail_next = True
        if not with_lookup:
            self.find_refund = None

    def find_refund(self, operation_id):
        return self._results.get(("refund", operation_id))

    def refund(self, operation_id, payment_ref, amount_minor=None):
        if self.fail_next:
            self.fail_next = False
            super().refund(operation_id, payment_ref, amount_minor)
            raise GatewayError("timeout")
        return super().refund(operation_id, payment_ref, amount_minor)


def test_uncertain_outcome_checks_external_state_before_retry():
    gw = FlakyGateway(with_lookup=True)
    s = svc(gw)
    r = s.request_refund("o1", "c1")
    assert r["state"] == "unknown"
    r = s.reconcile("o1")
    assert r["state"] == "confirmed"
    assert gw.calls == [("refund", "refund:o1")]  # no second provider call


def test_uncertain_outcome_retry_uses_same_operation_id():
    gw = FlakyGateway(with_lookup=False)
    s = svc(gw)
    assert s.request_refund("o1", "c1")["state"] == "unknown"
    assert s.request_refund("o1", "c1-again")["state"] == "confirmed"
    assert {op for _, op in gw.calls} == {"refund:o1"}


def test_pending_not_declared_confirmed():
    class Pending(MockGateway):
        def refund(self, operation_id, payment_ref, amount_minor=None):
            return RefundResult("re_p", "mock", False, "pending", amount_minor or 0, "usd",
                                operation_id, payment_ref)
    s = svc(Pending())
    r = s.request_refund("o1", "c1")
    assert r["state"] == "pending"
    assert s.get_order("o1").refunded_minor == 0


def test_failed_provider_status():
    class Failed(MockGateway):
        def refund(self, operation_id, payment_ref, amount_minor=None):
            return RefundResult("re_f", "mock", False, "failed", 0, "usd", operation_id, payment_ref)
    assert svc(Failed()).request_refund("o1", "c1")["state"] == "failed"


def test_unavailable_gateway_reports_setup():
    class Unavail(MockGateway):
        def refund(self, *a, **k):
            raise CapabilityUnavailable("refund", "no key", "set STRIPE_API_KEY to an sk_test_ key")
    s = svc(Unavail())
    r = s.request_refund("o1", "c1")
    assert r["state"] == "unavailable" and "sk_test_" in r["message"]


def test_livemode_result_rejected():
    from treasury.gateway import LiveModeError

    class Live(MockGateway):
        def refund(self, operation_id, payment_ref, amount_minor=None):
            return RefundResult("re_l", "mock", True, "succeeded", 1, "usd", operation_id, payment_ref)
    s = svc(Live())
    with pytest.raises(LiveModeError):
        s.request_refund("o1", "c1")
    assert s.get("o1")["state"] == "failed"


def test_routes():
    from treasury import routes_delivery
    from treasury.geo import Point
    from treasury.proof import SettlementService, seed_order
    gw = MockGateway()
    st = SettlementService(gw)
    st.add_order(seed_order("o9"))
    routes_delivery.set_service(st)
    routes_refunds.set_service(RefundService(gw))
    app = FastAPI()
    app.include_router(routes_refunds.router)
    c = TestClient(app)
    try:
        assert c.post("/refunds/orders/zz/cancel", json={"cancel_event_id": "e"}).status_code == 404
        # unfunded: client cannot conjure a payment
        assert c.post("/refunds/orders", json={"order_id": "o9", "payment_ref": "pi_9",
                      "amount_minor": 10000, "status": "canceled"}).status_code == 409
        st.fund_order("o9")
        body = c.post("/refunds/orders", json={"order_id": "o9", "payment_ref": "pi_evil",
                      "amount_minor": 1, "status": "canceled"}).json()
        assert body["amount_minor"] == 10000 and body["payment_ref"] != "pi_evil"
        r = c.post("/refunds/orders/o9/cancel", json={"cancel_event_id": "e1"}).json()
        assert r["state"] == "confirmed"
        g = c.get("/refunds/orders/o9").json()
        assert g["remaining_refundable_minor"] == 0 and g["refund"]["state"] == "confirmed"
        _ = Point
    finally:
        routes_refunds.set_service(None)
        routes_delivery.set_service(None)
