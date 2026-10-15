"""Integrator wiring: one audit log, routers mounted at root and /api, explanation, scenarios."""
from fastapi.testclient import TestClient

from treasury import routes_policies
from treasury.api import create_app
from treasury.gateway import MockGateway
from treasury.ledger import Ledger

ADMIN = {"X-Party-Role": "admin", "X-Party-Id": "t"}


def make():
    routes_policies.set_registry(None)
    led = Ledger(":memory:")
    return TestClient(create_app(led, MockGateway(), use_workflows=False)), led


def test_audit_routes_mounted_with_single_log_and_chain_verifies():
    c, led = make()
    assert c.post("/missions", json={"id": "m1", "amount_minor": 2000}).status_code == 200
    for prefix in ("", "/api"):
        assert c.get(prefix + "/audit/verify", headers=ADMIN).json()["data"]["ok"] is True
    assert c.get("/api/audit", headers=ADMIN).status_code == 200
    assert c.get("/api/audit").status_code == 401
    assert c.app.state.audit is led.audit
    routes_policies.set_registry(None)


def test_explanation_is_audit_sourced_without_cognee():
    c, _ = make()
    c.post("/missions", json={"id": "m2", "amount_minor": 2000})
    r = c.get("/api/payments/pay:m2/explanation", headers=ADMIN)
    assert r.status_code == 200
    b = r.json()
    assert b["source"] == "audit_log" and b["policy"]["hash"] and b["clauses"]
    assert c.get("/api/payments/pay:nope/explanation", headers=ADMIN).status_code == 404
    assert c.get("/api/payments/pay:m2/explanation").status_code == 401
    routes_policies.set_registry(None)


def test_four_scenarios_run_over_http_mock():
    c, _ = make()
    ids = [s["id"] for s in c.get("/api/scenarios").json()["data"]]
    for sid in ("deliver_and_release", "fuel_entitlement", "coverage_replan", "cancel_refund"):
        assert sid in ids
        r = c.post(f"/api/scenarios/{sid}/run", json={"run_id": f"r-{sid}", "mode": "mock"})
        assert r.status_code == 200, r.text
    assert c.post("/api/scenarios/cancel_refund/run", json={"mode": "stripe_test"}).status_code == 409
    assert c.get("/api/audit/verify", headers=ADMIN).json()["data"]["ok"] is True
    routes_policies.set_registry(None)
