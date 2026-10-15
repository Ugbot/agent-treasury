import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from treasury import routes_delivery
from treasury.gateway import CapabilityUnavailable, GatewayError, MockGateway
from treasury.geo import Point
from treasury.proof import (
    ProofError,
    ReceiptNonces,
    SettlementService,
    balance_view,
    seed_order,
)

DEST = Point(51.5, -0.12)
NEAR = Point(51.5003, -0.12)   # ~33 m
FAR = Point(51.502, -0.12)     # ~222 m


class FlakyGateway(MockGateway):
    def __init__(self, fail_dest=None, exc=None):
        super().__init__()
        self.fail_dest, self.exc = fail_dest, exc or GatewayError("boom")

    def transfer(self, operation_id, amount_minor, currency, destination, **kw):
        if destination == self.fail_dest:
            self.calls.append(("transfer_failed", operation_id))
            raise self.exc
        return super().transfer(operation_id, amount_minor, currency, destination, **kw)


def setup(gw=None, **kw):
    svc = SettlementService(gw or MockGateway(), **kw)
    o = svc.add_order(seed_order())
    svc.fund_order(o.id)
    return svc, o


def good(svc, now=100.0, **over):
    args = {"driver_id": "drv_1", "position": NEAR, "position_ts": now - 10, "recipient_id": "rcp_1",
                "nonce": svc.issue_nonce("ord_1", now), "now": now}
    args.update(over)
    return svc.release("ord_1", **args)


def transfers(gw):
    return [c for c in gw.calls if c[0] == "transfer"]


def test_seed_split_and_balance_view():
    o = seed_order()
    assert (o.total_minor, o.merchant_minor, o.driver_minor, o.retention_minor) == (10000, 7000, 2000, 1000)
    v = balance_view(o, stripe_fee_minor=59, fuel_cost_minor=300)
    assert v["platform_net_minor"] == 641 and v["stripe_fee_minor"] == 59
    assert v["fuel_operating_cost_minor"] == 300
    with pytest.raises(ProofError):
        seed_order(retention_minor=5)


def test_valid_proof_creates_two_distinct_transfers():
    gw = MockGateway()
    svc, _ = setup(gw)
    r = good(svc)
    assert r["status"] == "released" and r["atomic"] is False and r["bank_payout"] is False
    ops = {k: v["operation_id"] for k, v in r["legs"].items()}
    assert ops["merchant"] != ops["driver"]
    assert r["legs"]["merchant"]["amount_minor"] == 7000 and r["legs"]["driver"]["amount_minor"] == 2000
    assert r["legs"]["merchant"]["provider_ref"] != r["legs"]["driver"]["provider_ref"]
    assert r["evidence"]["policy_version"] == 1 and r["evidence"]["assignment_version"] == 1
    assert len(transfers(gw)) == 2


@pytest.mark.parametrize("over,gate", [
    ({"position": FAR}, "within_radius"),
    ({"position_ts": 10.0}, "position_fresh"),
    ({"position_ts": 200.0}, "position_fresh"),
    ({"driver_id": "drv_2"}, "assigned_driver"),
    ({"assignment_version": 2}, "assigned_driver"),
    ({"recipient_id": "rcp_other"}, "receipt_nonce"),
    ({"nonce": "bogus"}, "receipt_nonce"),
])
def test_all_release_gates_required(over, gate):
    gw = MockGateway()
    svc, _ = setup(gw)
    r = good(svc, **over)
    assert r["status"] in ("held", "denied") and not r["proven"]
    assert r["proof"]["gates"][gate] is False
    assert transfers(gw) == []


def test_every_gate_reported_even_when_several_fail():
    svc, _ = setup()
    r = good(svc, position=FAR, driver_id="x")
    g = r["proof"]["gates"]
    assert g["within_radius"] is False and g["assigned_driver"] is False
    assert g["receipt_nonce"] is True  # evaluated independently


def test_failed_attempt_does_not_burn_nonce_then_fresh_passes():
    svc, _ = setup()
    n = svc.issue_nonce("ord_1", 100.0)
    bad = svc.release("ord_1", driver_id="drv_1", position=FAR, position_ts=95, recipient_id="rcp_1",
                      nonce=n, now=100.0)
    assert not bad["proven"]
    ok = svc.release("ord_1", driver_id="drv_1", position=NEAR, position_ts=95, recipient_id="rcp_1",
                     nonce=n, now=101.0)
    assert ok["status"] == "released"


def test_nonce_expiry():
    svc, _ = setup()
    n = svc.issue_nonce("ord_1", 0.0)
    r = svc.release("ord_1", driver_id="drv_1", position=NEAR, position_ts=129, recipient_id="rcp_1",
                    nonce=n, now=130.0)
    assert r["proof"]["gates"]["receipt_nonce"] is False
    assert "expired" in r["proof"]["reasons"]["receipt_nonce"]


def test_nonce_replay_never_pays_again():
    gw = MockGateway()
    svc, _ = setup(gw)
    n = svc.issue_nonce("ord_1", 100.0)
    kw = {"driver_id": "drv_1", "position": NEAR, "position_ts": 95, "recipient_id": "rcp_1", "nonce": n, "now": 100.0}
    assert svc.release("ord_1", **kw)["status"] == "released"
    again = svc.release("ord_1", **kw)
    assert again["replay"] is True and len(transfers(gw)) == 2


def test_nonce_replay_on_unproven_second_order_rejected():
    nonces = ReceiptNonces()
    svc = SettlementService(MockGateway(), nonces)
    svc.add_order(seed_order("a"))
    svc.add_order(seed_order("b"))
    svc.fund_order("b")
    n = svc.issue_nonce("a", 100.0)
    r = svc.release("b", driver_id="drv_1", position=NEAR, position_ts=95, recipient_id="rcp_1",
                    nonce=n, now=100.0)
    assert r["proof"]["gates"]["receipt_nonce"] is False  # bound to order a


def test_nonce_rate_limited():
    svc, _ = setup()
    for i in range(5):
        svc.release("ord_1", driver_id="drv_1", position=NEAR, position_ts=99, recipient_id="rcp_1",
                    nonce=f"guess{i}", now=100.0 + i)
    n = svc.issue_nonce("ord_1", 106.0)
    r = svc.release("ord_1", driver_id="drv_1", position=NEAR, position_ts=105, recipient_id="rcp_1",
                    nonce=n, now=106.0)
    assert r["status"] == "held" and r["proof"]["reasons"]["receipt_nonce"] == "rate limited"
    # window passes -> allowed
    n2 = svc.issue_nonce("ord_1", 200.0)
    assert svc.release("ord_1", driver_id="drv_1", position=NEAR, position_ts=199, recipient_id="rcp_1",
                       nonce=n2, now=200.0)["status"] == "released"


def test_partial_split_does_not_repay_success():
    gw = FlakyGateway(fail_dest="acct_driver_test")
    svc, _ = setup(gw)
    r = good(svc)
    assert r["status"] == "partial" and r["atomic"] is False
    assert r["legs"]["merchant"]["status"] == "confirmed"
    assert r["legs"]["driver"]["status"] == "failed"
    merchant_ref = r["legs"]["merchant"]["provider_ref"]
    gw.fail_dest = None
    r2 = svc.retry("ord_1")
    assert r2["status"] == "released"
    assert r2["legs"]["merchant"]["provider_ref"] == merchant_ref
    assert r2["legs"]["merchant"]["attempts"] == 1 and r2["legs"]["driver"]["attempts"] == 2
    merchant_ops = [c for c in gw.calls if c == ("transfer", "settle:ord_1:merchant")]
    assert len(merchant_ops) == 1


def test_unavailable_gateway_reports_setup_message():
    gw = FlakyGateway("acct_merchant_test",
                      CapabilityUnavailable("transfer", "no connect", "enable Connect in test mode"))
    svc, _ = setup(gw)
    r = good(svc)
    assert r["status"] == "partial"
    assert "enable Connect" in r["legs"]["merchant"]["error"]


def test_over_limit_waits_for_bound_approval_and_invalidation():
    gw = MockGateway()
    svc, _ = setup(gw, auto_limit_minor=5000)
    r = good(svc)
    assert r["legs"]["merchant"]["status"] == "awaiting_approval"
    assert r["legs"]["driver"]["status"] == "confirmed"
    svc.approve("ord_1", "merchant", "ops")
    # policy change invalidates the approval
    svc.orders["ord_1"] = seed_order(policy_version=2)
    assert svc.retry("ord_1")["legs"]["merchant"]["status"] == "awaiting_approval"
    # recipient change also invalidates
    svc.approve("ord_1", "merchant", "ops")
    svc.orders["ord_1"] = seed_order(policy_version=2, merchant_destination="acct_other")
    assert svc.retry("ord_1")["legs"]["merchant"]["status"] == "awaiting_approval"
    svc.approve("ord_1", "merchant", "ops")
    assert svc.retry("ord_1")["status"] == "released"
    assert gw.calls.count(("transfer", "settle:ord_1:driver")) == 1


def test_routes_roundtrip():
    svc = SettlementService(MockGateway())
    routes_delivery.set_service(svc)
    app = FastAPI()
    app.include_router(routes_delivery.router)
    c = TestClient(app)
    try:
        assert c.post("/delivery/orders", json={"order_id": "o9"}).json()["balance"]["gross_minor"] == 10000
        assert c.post("/delivery/orders/o9/fund").status_code == 200
        n = c.post("/delivery/orders/o9/receipt-nonce", json={"now": 100}).json()["nonce"]
        body = {"driver_id": "drv_1", "lat": 51.5003, "lon": -0.12, "position_ts": 95, "recipient_id": "rcp_1",
                    "nonce": n, "now": 100}
        assert c.post("/delivery/orders/o9/release", json={**body, "lat": 51.502}).json()["status"] in ("denied", "held")
        assert c.post("/delivery/orders/o9/release", json=body).json()["status"] == "released"
        assert c.get("/delivery/orders/o9").json()["status"] == "released"
        assert c.get("/delivery/orders/none").json()["status"] == "held"
        assert c.post("/delivery/orders/none/receipt-nonce", json={"now": 1}).status_code == 404
    finally:
        routes_delivery.set_service(None)


# ---- money safety: funding gate and live CURRENCY-1 -----------------------------------

def test_release_requires_funded_order():
    gw = MockGateway()
    svc = SettlementService(gw)
    svc.add_order(seed_order())
    n = svc.issue_nonce("ord_1", 100.0)
    r = svc.release("ord_1", driver_id="drv_1", position=NEAR, position_ts=90, recipient_id="rcp_1",
                    nonce=n, now=100.0)
    assert r["status"] == "held" and r["reason"] == "order_not_funded" and r["legs"] == {}
    assert transfers(gw) == []
    svc.fund_order("ord_1")  # nonce was not burned by the held attempt
    r = svc.release("ord_1", driver_id="drv_1", position=NEAR, position_ts=90, recipient_id="rcp_1",
                    nonce=n, now=101.0)
    assert r["status"] == "released" and len(transfers(gw)) == 2


def test_unfunded_retry_never_transfers():
    gw = MockGateway()
    svc, _ = setup(gw)
    good(svc)
    svc.funding.clear()
    svc.settlements["ord_1"].legs["driver"].status = "pending"
    svc.retry("ord_1")
    assert svc.settlements["ord_1"].legs["driver"].error == "order_not_funded"
    assert len(transfers(gw)) == 2  # only the original two


@pytest.mark.parametrize("cur", ["jpy", "eur"])
def test_foreign_currency_denied_with_clause(cur):
    import sqlite3

    from treasury.audit import AuditLog
    from treasury.proof import PolicyDenied
    db = sqlite3.connect(":memory:")
    gw = MockGateway()
    svc = SettlementService(gw, audit=AuditLog(db))
    svc.add_order(seed_order(currency=cur))
    with pytest.raises(PolicyDenied) as ei:
        svc.fund_order("ord_1")
    assert ei.value.info["clause"] == "CURRENCY-1" and "CURRENCY-1" in str(ei.value)
    assert not [c for c in gw.calls if c[0] in ("payment", "create_payment")]
    r = svc.release("ord_1", driver_id="drv_1", position=NEAR, position_ts=90,
                    recipient_id="rcp_1", nonce="x", now=100.0)
    assert r["status"] in ("held", "denied") and transfers(gw) == []
    row = db.execute("SELECT mission_id, party, payload FROM audit_log WHERE kind='delivery.fund'").fetchone()
    assert row[0] == "ord_1" and row[1] == "rcp_1" and "CURRENCY-1" in row[2]


def test_release_denied_when_policy_changes_after_funding(monkeypatch):
    from treasury import policy as policy_mod
    gw = MockGateway()
    svc, _ = setup(gw)
    eur_policy = policy_mod.Policy("org-base", 2, tuple(
        policy_mod.Clause(c.id, c.prose, c.kind, {"currencies": ["eur"]}) if c.kind == "allowed_currency"
        else c for c in policy_mod.DEFAULT_POLICY.clauses))
    monkeypatch.setattr(policy_mod, "DEFAULT_POLICY", eur_policy)  # live lookup, not import-time copy
    r = good(svc)
    assert r["status"] == "denied" and r["clause"] == "CURRENCY-1" and transfers(gw) == []


def test_currency_from_registry_effective_policy():
    from treasury.policies import default_registry
    reg = default_registry()
    gw = MockGateway()
    svc = SettlementService(gw, registry=reg)
    svc.add_order(seed_order(currency="eur"))
    from treasury.proof import PolicyDenied
    with pytest.raises(PolicyDenied):
        svc.fund_order("ord_1")
