import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from treasury import policy_author as pa
from treasury import routes_freeze, routes_policies
from treasury.audit import AuditLog
from treasury.decision import clause_reasons
from treasury.policies import MandatoryError, default_registry
from treasury.policy import Clause, Policy, evaluate
from treasury.routes_freeze import seed_freeze

OK = {"receipt_confirmed": True, "prohibited_zone": False, "currency": "usd"}


def ev(clause, inputs, amount=100):
    return evaluate(Policy("p", 1, (clause,)), {**OK, **inputs}, 1, amount).results[0]


CP = Clause("PAYEE-1", "x", "allowed_counterparties", {"recipients": ["ann"], "categories": ["5541"]})


def test_counterparty_allow_hold_deny():
    assert ev(CP, {"recipient": "Ann", "merchant_category": "5541"})["outcome"] == "allow"
    assert ev(CP, {"merchant_category": "5541"})["outcome"] == "hold"
    r = ev(CP, {"recipient": "bob", "merchant_category": "5541"})
    assert r["outcome"] == "deny" and r["clause"] == "PAYEE-1"
    assert ev(CP, {"recipient": "ann", "merchant_category": "9999"})["outcome"] == "deny"


def test_authoring_validation_and_sentences():
    bad = [{"id": "A-1", "prose": "x", "kind": "allowed_counterparties", "params": {"recipients": [], "categories": []}}]
    assert pa.validate_clauses(bad)
    bad = [{"id": "A-1", "prose": "x", "kind": "velocity_limit", "params": {"max_payments": 0, "window_seconds": 60}}]
    assert pa.validate_clauses(bad)
    assert pa.sentence({"kind": "velocity_limit", "params": {"max_payments": 3, "window_seconds": 600}}) == (
        "No more than 3 payments every 10 minutes", "hold")
    assert pa.sentence({"kind": "payment_freeze", "params": {"active": True}})[0] == "Payments are frozen"
    assert pa.sentence({"kind": "allowed_counterparties", "params": {"recipients": ["a", "b"], "categories": []}})[0] \
        == "Only pay these payees: a, b"
    kinds = {k["kind"] for k in pa.CLAUSE_KINDS}
    assert {"allowed_counterparties", "velocity_limit", "payment_freeze"} <= kinds


def test_narrowing_ok_widening_refused_for_mandatory():
    reg = default_registry()
    b = reg.publish("org-payees", "Payees", [CP])
    reg.attach("organisation", "org", "org-payees", 1, reg.target_version("organisation", "org"), mandatory=True)

    def cl(r, c):
        return [Clause("PAYEE-1", "x", "allowed_counterparties", {"recipients": r, "categories": c})]
    v = reg.publish("org-payees", "Payees", cl(["ann"], ["5541"]), b.version)
    v2 = reg.publish("org-payees", "Payees", cl(["ann", "bob"], ["5541"]), v.version)
    with pytest.raises(MandatoryError):
        reg.attach("organisation", "org", "org-payees", v2.version, reg.target_version("organisation", "org"), True)
    v3 = reg.publish("org-payees", "Payees", cl([], ["5541"]), v2.version)  # drops payees: widening
    with pytest.raises(MandatoryError):
        reg.attach("organisation", "org", "org-payees", v3.version, reg.target_version("organisation", "org"), True)
    v4 = reg.publish("org-payees", "Payees", cl(["ann"], []), v3.version)
    with pytest.raises(MandatoryError):
        reg.attach("organisation", "org", "org-payees", v4.version, reg.target_version("organisation", "org"), True)
    w = [Clause("VEL-1", "x", "velocity_limit", {"max_payments": 3, "window_seconds": 600})]
    assert pa.weakenings(reg, "nope", []) == []
    reg.publish("vel", "v", w)
    assert pa._weakens(tuple(w), (Clause("VEL-1", "x", "velocity_limit", {"max_payments": 2, "window_seconds": 900}),)) is None
    assert pa._weakens(tuple(w), (Clause("VEL-1", "x", "velocity_limit", {"max_payments": 4, "window_seconds": 600}),))
    assert pa._weakens(tuple(w), (Clause("VEL-1", "x", "velocity_limit", {"max_payments": 3, "window_seconds": 60}),))


def test_velocity_window():
    c = Clause("VEL-1", "x", "velocity_limit", {"max_payments": 2, "window_seconds": 600})
    now = 10_000
    assert ev(c, {"now": now, "payment_times": [now - 10]})["outcome"] == "allow"
    assert ev(c, {"now": now, "payment_times": [now - 10, now - 20]})["outcome"] == "hold"
    # resets once they fall out of the window
    assert ev(c, {"now": now + 601, "payment_times": [now - 10, now - 20]})["outcome"] == "allow"
    assert ev(c, {})["outcome"] == "hold"


def make_app(tmp_path):
    from treasury.ledger import Ledger
    led = Ledger(tmp_path / "t.sqlite")
    reg = default_registry()
    routes_policies.set_registry(reg)
    app = FastAPI()
    app.state.audit = led.audit
    app.include_router(routes_freeze.router)
    return TestClient(app), reg, led


def test_freeze_holds_everything_and_only_approver_unfreezes(tmp_path):
    c, reg, led = make_app(tmp_path)
    try:
        assert c.get("/freeze").json()["data"]["active"] is False
        body = {"active": True, "reason": "incident"}
        assert c.post("/freeze", json=body).status_code == 403
        assert c.post("/freeze", json=body, headers={"X-Actor-Role": "approver", "X-Actor-Id": "ann"}).json()["data"]["active"]
        d = reg.decide("mission", "m1", {**OK}, 1, 100)
        assert d.verdict == "hold" and any("frozen" in str(r["evidence"]) for r in d.results)
        # even a tiny, otherwise approved amount is held
        assert reg.decide("mission", "m2", {**OK}, 1, 1).verdict == "hold"
        # not weakenable through the generic publish/attach path
        from treasury.policies import _weakens
        on = (Clause("FREEZE-1", "x", "payment_freeze", {"active": True}),)
        off = (Clause("FREEZE-1", "x", "payment_freeze", {"active": False}),)
        assert _weakens(on, off) and _weakens(off, on) is None
        assert c.post("/freeze", json={"active": False, "reason": "all clear"}).status_code == 403
        r = c.post("/freeze", json={"active": False, "reason": "all clear"}, headers={"X-Actor-Role": "approver"})
        assert r.json()["data"]["active"] is False and r.json()["data"]["reason"] == "all clear"
        assert reg.decide("mission", "m3", {**OK}, 1, 100).verdict == "allow"
        rows = led.audit.query(kind="policy.freeze")
        assert [x["payload"]["active"] for x in rows] == [True, False]
        assert rows[1]["payload"]["reason"] == "all clear" and rows[0]["actor"] == "ann"
    finally:
        routes_policies.set_registry(None)


def test_dry_run_situations():
    reg = default_registry()
    cl = [{"id": "PAYEE-1", "prose": "x", "kind": "allowed_counterparties",
           "params": {"recipients": ["ann"], "categories": []}}]
    out = {r["situation"]: r for r in pa.dry_run(reg, cl, ["all_in_order", "payee_not_allowed", "frozen"])}
    assert out["all_in_order"]["verdict"] == "allow"
    assert out["payee_not_allowed"]["verdict"] == "deny" and out["payee_not_allowed"]["clause"] == "PAYEE-1"
    assert out["frozen"]["verdict"] == "allow"
    fr = [{"id": "F-1", "prose": "x", "kind": "payment_freeze", "params": {"active": True}}]
    assert pa.dry_run(reg, fr, ["frozen"])[0]["verdict"] == "hold"


def test_fuel_card_declined_by_clause(tmp_path):
    from treasury.fuel import FuelService
    from treasury.gateway import MockGateway
    from treasury.geo import Point
    reg = default_registry()
    p1 = reg.publish("org-payees", "Payees", [Clause("PAYEE-1", "x", "allowed_counterparties",
                                                     {"recipients": [], "categories": ["5411"]})])
    reg.attach("organisation", "org", "org-payees", p1.version, reg.target_version("organisation", "org"), True)
    db = __import__("sqlite3").connect(":memory:")
    audit = AuditLog(db)
    route = (Point(51.5000, -0.1000), Point(51.5100, -0.1000))
    svc = FuelService(MockGateway(), stops={"shell": Point(51.5050, -0.1000)}, clock=lambda: 1000.0,
                      issuing_balance_minor=5000, registry=reg, audit=audit)
    svc.update_job("j1", status="active", driver_id="d1", assignment_version=1, route=route,
                   position=Point(51.5049, -0.1000), position_ts=995.0)
    e = svc.issue("j1", "d1", 1)
    rec = svc.decide("a1", e.card_ref, 100, "usd", "5541", "GB", "shell", 1000.0)
    assert not rec.approved and "merchant_category_not_permitted:PAYEE-1" in rec.reasons
    seed_freeze(reg)
    assert clause_reasons(reg, {"merchant_category": "5411", "recipient": "x"}) == []


def test_template_seeded():
    from treasury.jobs import TEMPLATES
    assert "tpl-approved-payees" in TEMPLATES
    assert time.time() > 0
