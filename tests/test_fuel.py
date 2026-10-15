import json
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from treasury import routes_fuel
from treasury.fuel import (
    FuelPolicy,
    FuelService,
    SignatureError,
    sign_payload,
    verify_signature,
)
from treasury.gateway import CapabilityUnavailable, LiveModeError, MockGateway
from treasury.geo import Point

ROUTE = (Point(51.5000, -0.1000), Point(51.5100, -0.1000))
STOP = Point(51.5050, -0.1000)
FAR = Point(51.5050, -0.0900)
NOW = 1000.0


def make(**kw):
    svc = FuelService(MockGateway(), stops={"shell": STOP, "far": FAR}, clock=lambda: NOW,
                      issuing_balance_minor=kw.pop("balance", 5000), **kw)
    svc.update_job("j1", status="active", driver_id="d1", assignment_version=1,
                   route=ROUTE, position=Point(51.5049, -0.1000), position_ts=NOW - 5)
    e = svc.issue("j1", "d1", 1)
    return svc, e


def go(svc, e, amt, cat="5541", country="GB", m="shell"):
    return svc.attempt(e.entitlement_id, amt, cat, country, m)


def test_trip_fuel_entitlement_boundaries():
    svc, e = make()
    assert not e.spending  # creation is not spending
    r = go(svc, e, 500)
    assert r.approved
    c = svc.capture(r.authorization_id)
    assert c.issuing_transaction.startswith("ipi_")
    assert e.spending and e.captured_minor == 500
    assert svc.reconcile(e.entitlement_id)["consistent"]
    assert go(svc, e, 300).approved  # exactly to the cap of 800
    r2 = go(svc, e, 1)
    assert "over_limit" in r2.reasons


@pytest.mark.parametrize("kwargs,reason", [
    ({"amt": 900}, "over_limit"),
    ({"amt": 500, "cat": "5812"}, "merchant_category_not_permitted"),
    ({"amt": 500, "country": "FR"}, "country_not_permitted"),
    ({"amt": 500, "m": "far"}, "out_of_route"),
    ({"amt": 500, "m": "nowhere"}, "stop_not_on_permitted_route"),
])
def test_declines(kwargs, reason):
    svc, e = make()
    amt = kwargs.pop("amt")
    r = go(svc, e, amt, **kwargs)
    assert not r.approved and reason in r.reasons
    assert e.reserved_minor == 0


def test_stale_position():
    svc, e = make()
    svc.update_job("j1", position_ts=NOW - 600)
    assert "stale_position" in go(svc, e, 500).reasons


@pytest.mark.parametrize("change", [
    {"status": "completed"}, {"status": "unassigned"},
    {"driver_id": "d2"}, {"assignment_version": 2}, {"coverage_valid": False}])
def test_inactive_reassigned_coverage(change):
    svc, e = make()
    svc.update_job("j1", **change)
    r = go(svc, e, 500)
    assert not r.approved
    assert e.state == "revoked" and e.remaining_minor == 0


def test_invalid_coverage_blocks_issue():
    svc = FuelService(MockGateway(), clock=lambda: NOW, issuing_balance_minor=1000)
    svc.update_job("j", status="active", driver_id="d", assignment_version=1, coverage_valid=False)
    with pytest.raises(ValueError):
        svc.issue("j", "d", 1)


def test_unfunded_issuing_balance():
    svc, e = make(balance=None)
    assert "issuing_balance_unverified" in go(svc, e, 500).reasons
    svc.set_issuing_balance(100)
    assert "issuing_balance_insufficient" in go(svc, e, 500).reasons


def test_aggregate_budget():
    svc, e = make(policy=FuelPolicy(budget_minor=600))
    svc.update_job("j2", status="active", driver_id="d2", assignment_version=1,
                   route=ROUTE, position=STOP, position_ts=NOW)
    e2 = svc.issue("j2", "d2", 1)
    assert go(svc, e, 500).approved
    assert "aggregate_budget_exceeded" in go(svc, e2, 500).reasons


def test_concurrent_attempts_share_cap():
    svc, e = make()
    results = []

    def worker():
        results.append(go(svc, e, 300).approved)

    ts = [threading.Thread(target=worker) for _ in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(results) == 2  # 600 <= 800 < 900
    assert e.reserved_minor == 600


def test_revoke_keeps_outstanding_reconciled():
    svc, e = make()
    r = go(svc, e, 500)
    svc.update_job("j1", driver_id="d2")  # reassign
    assert e.state == "revoked" and e.remaining_minor == 0
    assert not go(svc, e, 100).approved
    c = svc.capture(r.authorization_id)  # outstanding still captures
    assert c.state == "captured"
    assert svc.reconcile(e.entitlement_id)["consistent"]


def test_policy_change_invalidates_unused():
    svc, e = make()
    assert svc.update_policy(FuelPolicy(version=2, cap_minor=600)) == 1
    assert not go(svc, e, 500).approved
    e2 = svc.issue("j1", "d1", 1)
    assert e2.cap_minor == 600
    assert "over_limit" in go(svc, e2, 700).reasons


def test_provider_decline_releases_reservation():
    svc, e = make()
    svc.gateway.fail_authorizations_over_minor = 100
    r = go(svc, e, 500)
    assert not r.approved and e.reserved_minor == 0


def test_decision_path_makes_no_gateway_calls():
    svc, e = make()
    n = len(svc.gateway.calls)
    rec = svc.decide("x1", e.card_ref, 500, "usd", "5541", "GB", "shell")
    assert rec.approved and len(svc.gateway.calls) == n


def test_labels():
    _, e = make()
    assert "Simulated" in e.labels["merchant"] and "basket" in e.labels["category"]


def event(card, amount=500, cat="5541"):
    return json.dumps({"type": "issuing_authorization.request", "livemode": False, "data": {"object": {
        "id": "iauth_1", "currency": "usd", "livemode": False, "card": {"id": card},
        "pending_request": {"amount": amount},
        "merchant_data": {"category_code": cat, "country": "GB", "network_id": "shell"}}}}).encode()


def test_webhook_signature_and_decision():
    svc, e = make()
    body = event(e.card_ref)
    h = sign_payload(body, "whsec_t", int(NOW))
    assert svc.handle_webhook(body, h, "whsec_t")["approved"] is True
    with pytest.raises(SignatureError):
        svc.handle_webhook(body, h, "whsec_other")
    with pytest.raises(SignatureError):
        svc.handle_webhook(body, None, "whsec_t")
    with pytest.raises(SignatureError):
        verify_signature(body, sign_payload(body, "whsec_t", 1), "whsec_t", NOW)
    with pytest.raises(CapabilityUnavailable) as ei:
        svc.handle_webhook(body, h, None)
    assert "STRIPE_WEBHOOK_SECRET" in ei.value.setup


def test_webhook_rejects_livemode():
    svc, _e = make()
    body = json.dumps({"type": "x", "livemode": True, "data": {"object": {}}}).encode()
    with pytest.raises(LiveModeError):
        svc.handle_webhook(body, sign_payload(body, "s", int(NOW)), "s")


def test_routes(monkeypatch):
    svc, e = make()
    routes_fuel.set_service(svc)
    app = FastAPI()
    app.include_router(routes_fuel.router)
    c = TestClient(app)
    r = c.post(f"/fuel/entitlements/{e.entitlement_id}/attempts",
               json={"amount_minor": 500, "category": "5541", "country": "GB", "merchant_id": "shell"})
    assert r.json()["approved"]
    cap = c.post(f"/fuel/authorizations/{r.json()['authorization_id']}/capture", json={})
    assert cap.json()["state"] == "captured"
    assert c.get(f"/fuel/entitlements/{e.entitlement_id}").json()["captured_minor"] == 500
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    assert c.post("/fuel/webhook", content=b"{}").status_code == 503
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "s")
    assert c.post("/fuel/webhook", content=b"{}", headers={"stripe-signature": "bad"}).status_code == 400
    routes_fuel.set_service(None)


# ---- money safety ----------------------------------------------------------------------

def _signed(svc, obj, top=None):
    body = json.dumps({"type": "issuing_authorization.request", "livemode": False,
                       "data": {"object": obj}, **(top or {})}).encode()
    return body, sign_payload(body, "whsec_t", int(NOW))


@pytest.mark.parametrize("nested", [
    {"id": "iauth_1", "livemode": False, "card": {"id": "x", "livemode": True}},
    {"id": "iauth_1", "livemode": False, "merchant_data": {"deep": [{"livemode": True}]}},
])
def test_webhook_rejects_nested_livemode(nested):
    svc, _e = make()
    body, h = _signed(svc, nested)
    with pytest.raises(LiveModeError):
        svc.handle_webhook(body, h, "whsec_t")
    assert svc.auths == {}


def test_webhook_rejects_livemode_in_other_event_shape():
    svc, _ = make()
    body = json.dumps({"type": "other", "livemode": False,
                       "data": {"object": {"x": {"livemode": True}}}}).encode()
    with pytest.raises(LiveModeError):
        svc.handle_webhook(body, sign_payload(body, "whsec_t", int(NOW)), "whsec_t")


class StatusGateway(MockGateway):
    def __init__(self, status):
        super().__init__()
        self.status = status

    def release_authorization(self, operation_id, authorization_ref):
        from treasury.gateway import FuelReleaseResult
        return FuelReleaseResult(authorization_ref, "mock", False, self.status,
                                 authorization_ref=authorization_ref)


def _reserved(status):
    svc = FuelService(StatusGateway(status), stops={"shell": STOP}, clock=lambda: NOW,
                      issuing_balance_minor=5000)
    svc.update_job("j1", status="active", driver_id="d1", assignment_version=1, route=ROUTE,
                   position=Point(51.5049, -0.1000), position_ts=NOW - 5)
    e = svc.issue("j1", "d1", 1)
    r = go(svc, e, 500)
    assert r.approved and r.provider_ref
    return svc, e, r


@pytest.mark.parametrize("status", ["reversed", "closed", "expired"])
def test_release_frees_headroom_only_on_provider_confirmation(status):
    svc, e, r = _reserved(status)
    out = svc.release(r.authorization_id)
    assert out.state == "released" and not out.release_unresolved and e.reserved_minor == 0


@pytest.mark.parametrize("status", ["pending", "pending_review", "failed", ""])
def test_release_unconfirmed_keeps_reservation(status):
    import sqlite3

    from treasury.audit import AuditLog
    db = sqlite3.connect(":memory:")
    svc, e, r = _reserved(status)
    svc.audit = AuditLog(db)
    out = svc.release(r.authorization_id)
    assert out.state == "reserved" and out.release_unresolved and out.release_status == status
    assert e.reserved_minor == 500 and svc.reconcile(e.entitlement_id)["consistent"]
    assert "over_limit" in go(svc, e, 400).reasons  # 500 still reserved
    row = db.execute("SELECT mission_id, party FROM audit_log WHERE kind='fuel.release.unresolved'").fetchone()
    assert row == ("j1", "d1")


def test_fuel_foreign_currency_denied_live(monkeypatch):
    from treasury import policy as pm
    svc, e = make()
    rec = svc.decide("a1", e.card_ref, 100, "jpy", "5541", "GB", "shell", NOW)
    assert not rec.approved and "currency_not_permitted" in rec.reasons
    monkeypatch.setattr(pm, "DEFAULT_POLICY", pm.Policy("org-base", 2, tuple(
        pm.Clause(c.id, c.prose, c.kind, {"currencies": ["eur"]}) if c.kind == "allowed_currency" else c
        for c in pm.DEFAULT_POLICY.clauses)))
    rec = svc.decide("a2", e.card_ref, 100, "usd", "5541", "GB", "shell", NOW)
    assert "currency_not_permitted:CURRENCY-1" in rec.reasons
    svc2 = FuelService(MockGateway(), FuelPolicy(currency="usd"), clock=lambda: NOW)
    svc2.update_job("j2", status="active", driver_id="d1", assignment_version=1)
    with pytest.raises(ValueError, match="CURRENCY-1"):
        svc2.issue("j2", "d1", 1)
