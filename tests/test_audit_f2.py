"""Audit/explain fixes: scenario explanations, party scoping, event ordering, attribution,
unavailable/approval audit, redaction and the verify anchor."""
import json

import pytest
from fastapi.testclient import TestClient

from treasury import routes_policies
from treasury.api import create_app
from treasury.gateway import MockGateway
from treasury.ledger import Ledger
from treasury.policies import default_registry
from treasury.scenarios import ScenarioError, ScenarioRunner, build_runtime
from treasury.stripe_gateway import StripeGateway, audit_scope

ADMIN = {"X-Party-Role": "admin", "X-Party-Id": "t"}


def hdr(role, pid=None):
    return {"X-Party-Role": role, "X-Party-Id": pid or role}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("STRIPE_CONNECT_ACCOUNT_A", "STRIPE_CONNECT_ACCOUNT_B", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    yield
    routes_policies.set_registry(None)


def app():
    routes_policies.set_registry(None)
    led = Ledger(":memory:")
    return TestClient(create_app(led, MockGateway(), use_workflows=False)), led


def paid_run(c, sid="r1"):
    c.post("/api/scenarios/deliver_and_release/run", json={"run_id": sid, "use_llm": False})
    r = c.post("/api/scenarios/deliver_and_release/run",
               json={"run_id": sid, "approvals": ["merchant"], "use_llm": False})
    body = r.json()
    return body.get("data", body)


def test_explanation_for_scenario_mission_has_policy_clauses_stripe_and_seqs():
    c, _ = app()
    run = paid_run(c)
    assert run["status"] == "completed", run
    mid = run["mission_id"]
    for pid in (f"settle:ord-{mid}:merchant", f"settle:ord-{mid}:driver", mid):
        r = c.get(f"/api/payments/{pid}/explanation", headers=ADMIN)
        assert r.status_code == 200, (pid, r.text)
        b = r.json()
        assert b["mission_id"] == mid and b["decision_kind"] in ("agent.verdict", "decision")
        assert b["policy"]["id"] and b["policy"]["hash"] and b["clauses"] and b["auditSeqs"]
        assert b["evidence"] and b["stripe"]["ids"] and b["stripe"]["livemode"] is False
    m = c.get(f"/api/payments/settle:ord-{mid}:driver/explanation", headers=ADMIN).json()
    assert "pay_driver" in json.dumps(m["evidence"])


def test_explain_is_party_scoped():
    c, _ = app()
    mid = paid_run(c)["mission_id"]
    drv = f"/api/payments/settle:ord-{mid}:driver/explanation"
    mer = f"/api/payments/settle:ord-{mid}:merchant/explanation"
    assert c.get(drv).status_code == 401
    r = c.get(drv, headers=hdr("driver"))
    assert r.status_code == 200
    assert all(e["party"] == "driver" for e in r.json()["entries"])
    assert c.get(mer, headers=hdr("driver")).status_code == 404  # not this party's decision
    assert c.get(mer, headers=hdr("receiver")).status_code == 200
    assert c.get(drv, headers=hdr("admin")).status_code == 200


def test_input_event_precedes_replan_and_is_not_duplicated():
    c, led = app()
    c.post("/api/scenarios/coverage_replan/run", json={"run_id": "cr"})
    ents = led.audit.query(mission_id="scn-cr", limit=1000)
    evs = [e for e in ents if e["kind"] == "input.event"]
    ids = [e["payload"]["event_id"] for e in evs]
    assert len(ids) == len(set(ids)) and ids
    cover_a = next(e for e in evs if e["payload"]["event_id"].endswith("cover-a"))
    first_replan = next(e for e in ents if e["kind"] == "replan")
    assert cover_a["seq"] < first_replan["seq"]
    refused = next(e for e in ents if e["kind"] == "plan.refused")
    assert cover_a["seq"] < refused["seq"]  # the cause is logged before the refusal it triggers


def test_every_scenario_entry_has_mission_and_party():
    c, led = app()
    for sid in ("deliver_and_release", "fuel_entitlement", "coverage_replan", "cancel_refund"):
        c.post(f"/api/scenarios/{sid}/run", json={"run_id": f"x-{sid}", "use_llm": False})
    rows = [e for e in led.audit.query(limit=10000) if (e["mission_id"] or "").startswith("scn-")]
    assert rows
    bad = [(e["seq"], e["kind"]) for e in rows if not e["mission_id"] or not e["party"]]
    assert not bad, bad
    # fuel.py (another stream) attributes its own entries to the driver id (e.g. drv_1), not the role
    assert {e["party"] for e in rows} - {"drv_1"} <= {"sender", "receiver", "driver", "approver"}


def test_stripe_call_entries_get_mission_and_party_from_context():
    led = Ledger(":memory:")
    gw = StripeGateway(client=object(), audit=led.audit)
    with audit_scope("m-ctx", "driver"):
        gw._record("transfer", "op1", "idem", "ok", ["tr_1"], False)
    gw._record("transfer", "op2", "idem", "ok", ["tr_2"], False)
    a, b = led.audit.query(kind="stripe.call")
    assert (a["mission_id"], a["party"]) == ("m-ctx", "driver")
    assert (b["mission_id"], b["party"]) == (None, None)  # scope does not leak


def test_unavailable_scenario_audits_what_and_the_fix():
    gw = MockGateway()
    gw.mode = "stripe_test"  # a stripe_test gateway without connected-account env vars
    rt = build_runtime(Ledger(":memory:"), gw, default_registry())
    runner = ScenarioRunner(rt)
    r = runner.run("deliver_and_release", "u1")
    assert r["status"] == "unavailable"
    e = rt.audit.query(mission_id="scn-u1", kind="scenario.unavailable")
    assert len(e) == 1 and e[0]["party"] == "sender"
    assert "STRIPE_CONNECT_ACCOUNT_A" in e[0]["payload"]["setup"]
    runner.run("deliver_and_release", "u1")  # re-reading does not duplicate
    assert len(rt.audit.query(mission_id="scn-u1", kind="scenario.unavailable")) == 1


def test_partial_approval_that_raises_leaves_no_orphan():
    rt = build_runtime(Ledger(":memory:"), MockGateway(), default_registry())
    rt.use_llm = False
    runner = ScenarioRunner(rt)
    v = runner.run("deliver_and_release", "p1")
    assert v["status"] == "awaiting_approval"
    before = len(rt.audit.query(mission_id="scn-p1", kind="approval"))
    with pytest.raises(ScenarioError):
        runner.run("deliver_and_release", "p1", ["merchant", "nonsense"])
    assert len(rt.audit.query(mission_id="scn-p1", kind="approval")) == before
    assert not runner.get("p1").approved
    refused = rt.audit.query(mission_id="scn-p1", kind="approval.refused")
    assert len(refused) == 1 and refused[0]["party"] == "approver"
    assert rt.audit.verify()["ok"]


def test_secrets_redacted_before_storing_events_and_inputs():
    led = Ledger(":memory:")
    led.create_mission("m1", 1000, "usd")
    secret = "sk_test_" + "A1b2C3d4E5f6G7"
    led.apply_event("e1", "m1", 0, {"note": f"key {secret}", "n": {secret: 1}})
    stored = json.dumps([dict(r) for r in led.db.execute("SELECT * FROM events")])
    stored += led.mission("m1")["inputs"]
    stored += json.dumps(led.audit.query(mission_id="m1"))
    assert secret not in stored and "[REDACTED]" in stored


def test_verify_returns_head_count_and_detects_truncation():
    led = Ledger(":memory:")
    for i in range(3):
        led.audit.append("system", "x", f"e{i}")
    v = led.audit.verify()
    assert v["ok"] and v["entries"] == 3 and len(v["head"]) == 64
    assert led.audit.verify(expected_head=v["head"], expected_count=3)["ok"]
    led.db.execute("DELETE FROM audit_log WHERE seq=(SELECT MAX(seq) FROM audit_log)")
    led.db.commit()
    assert led.audit.verify()["ok"]  # a truncated chain is internally valid ...
    t = led.audit.verify(expected_head=v["head"], expected_count=3)  # ... but not against the anchor
    assert not t["ok"] and t["anchor_mismatch"]


def test_verify_route_accepts_anchor_params_admin_sees_head():
    c, _ = app()
    c.post("/missions", json={"id": "m1", "amount_minor": 2000})
    d = c.get("/api/audit/verify", headers=ADMIN).json()["data"]
    ok = c.get("/api/audit/verify", headers=ADMIN,
               params={"expected_head": d["head"], "expected_count": d["entries"]}).json()["data"]
    assert ok["ok"] and ok["anchor_checked"]
    bad = c.get("/api/audit/verify", headers=ADMIN, params={"expected_count": d["entries"] + 5}).json()["data"]
    assert not bad["ok"] and bad["anchor_mismatch"]
    nonadmin = c.get("/api/audit/verify", headers=hdr("driver")).json()["data"]
    assert "head" not in nonadmin
