from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from treasury import routes_agent
from treasury.agent import GOAP_ONLY_NOTE
from treasury.gateway import MockGateway
from treasury.ledger import Ledger
from treasury.policies import default_registry
from treasury.scenarios import (
    SCENARIO_IDS,
    ScenarioError,
    ScenarioRunner,
    build_runtime,
)


class FakeLLM:
    def __init__(self, *calls):
        self.calls = list(calls)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        name, inp = self.calls.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name=name, input=inp)])


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("STRIPE_CONNECT_ACCOUNT_A", "STRIPE_CONNECT_ACCOUNT_B", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def make(gateway=None, llm=None):
    rt = build_runtime(Ledger(":memory:"), gateway or MockGateway(), default_registry(), llm_client=llm)
    return rt, ScenarioRunner(rt)


def kinds(view):
    return [m["kind"] for m in view["money"]]


def audit_kinds(rt, mission):
    return {(e["actor"], e["kind"]) for e in rt.audit.query(mission_id=mission, limit=2000)}


def test_deliver_and_release_stops_for_approval_then_pays():
    rt, r = make()
    v = r.run("deliver_and_release", "d1")
    assert v["label"] == "simulated" and v["agent"]["agent_mode"] == "goap_only"
    assert v["status"] == "awaiting_approval"
    assert kinds(v) == ["collect", "pay_driver"]           # merchant 7000 > APPROVE-1 limit, not paid
    need = v["approvals_needed"]
    assert [(n["leg"], n["amount_minor"], n["currency"]) for n in need] == [("merchant", 7000, "usd")]
    statuses = [s["status"] for s in v["steps"]]
    assert statuses[0] == "held"                            # premature attempt before proof was held
    v2 = r.run("deliver_and_release", "d1", ["merchant"])
    assert v2["status"] == "completed" and kinds(v2) == ["collect", "pay_driver", "pay_merchant"]
    assert not v2["approvals_needed"]
    assert all(m["livemode"] is False for m in v2["money"])
    ak = audit_kinds(rt, v["mission_id"])
    assert {("agent", "agent.proposal"), ("planner", "agent.verdict"), ("approver", "approval"),
            ("stripe", "stripe.call"), ("system", "delivery.proof"), ("planner", "plan")} <= ak
    assert rt.audit.verify()["ok"]
    # ledger holds one payment row per effect (idempotent operation ids)
    n = rt.ledger.db.execute("SELECT COUNT(*) FROM payments WHERE mission_id=?", (v["mission_id"],)).fetchone()[0]
    assert n == 3
    # replay is a no-op
    assert kinds(r.run("deliver_and_release", "d1")) == ["collect", "pay_driver", "pay_merchant"]


def test_approval_requires_pending_request():
    _, r = make()
    with pytest.raises(ScenarioError):
        r.run("deliver_and_release", "d2", ["merchant"])
    with pytest.raises(ScenarioError):
        r.run("nope")


def test_approval_binds_to_amount_and_policy():
    from treasury.agent import PolicyVerdict, Proposal
    from treasury.scenarios import _digest
    rt, r = make()
    r.run("deliver_and_release", "d3")
    run = r.get("d3")
    p = Proposal(kind="pay_merchant", amount_minor=7000, currency="usd")
    v = PolicyVerdict("hold", {}, effective_hash=run.pending["pay_merchant"]["policy_hash"])
    assert _digest(run, p, v) == run.pending["pay_merchant"]["digest"]
    assert _digest(run, p.model_copy(update={"amount_minor": 6999}), v) != run.pending["pay_merchant"]["digest"]
    assert _digest(run, p, PolicyVerdict("hold", {}, effective_hash="other")) != run.pending["pay_merchant"]["digest"]
    run.approved["pay_merchant"] = "forged"
    v2 = r.run("deliver_and_release", "d3")             # forged approval cannot release the merchant leg
    assert "pay_merchant" not in kinds(v2) and rt.audit.verify()["ok"]


def test_stripe_mode_without_connected_accounts_is_unavailable_not_faked():
    _, r = make(MockGateway(mode="stripe_test"))
    v = r.run("deliver_and_release", "d4")
    assert v["status"] == "unavailable" and "STRIPE_CONNECT_ACCOUNT_A" in v["unavailable"]
    assert v["money"] == []


def test_llm_cannot_pay_outside_the_plan():
    llm = FakeLLM(*[("pay_merchant", {"amount_minor": 1, "reason": "cheeky"})] * 8)
    _, r = make(llm=llm)
    v = r.run("deliver_and_release", "d5")
    assert v["agent"]["agent_mode"] == "llm+goap"
    assert "pay_merchant" not in kinds(v)
    assert any(s["status"] == "blocked_by_plan" for s in v["steps"])


def test_fuel_entitlement_scenario():
    rt, r = make()
    v = r.run("fuel_entitlement", "f1")
    assert v["status"] == "completed"
    cases = {s["case"]: s for s in v["steps"] if s["status"] == "card_attempt"}
    assert cases["on-route stop, within cap"]["approved"] is True
    assert "over_limit" in cases["over the remaining cap"]["reasons"]
    assert "merchant_category_not_permitted" in cases["wrong merchant category"]["reasons"]
    assert cases["after coverage lapse"]["approved"] is False
    assert [m["amount_minor"] for m in v["money"]] == [600]
    ak = audit_kinds(rt, v["mission_id"])
    assert ("system", "fuel.issue") in ak and ("planner", "replan") in ak


def test_coverage_replan_scenario_completes_and_pays_driver_b_once():
    rt, r = make()
    v = r.run("coverage_replan", "c1")
    mid = v["mission_id"]
    assert v["status"] == "completed"
    assert kinds(v) == ["collect", "pay_driver", "pay_merchant"]
    st = [s["status"] for s in v["steps"]]
    assert st[0] == "blocked_by_plan" and "held" in st
    replans = rt.audit.query(mission_id=mid, kind="replan")
    assert len(replans) == 1
    assert replans[0]["payload"]["old_plan"]["driver_id"] == "drv_1"
    assert replans[0]["payload"]["new_plan"]["driver_id"] == "drv_2"
    assert replans[0]["payload"]["new_plan"]["status"] == "planned"
    assert replans[0]["payload"]["policy_weakened"] is False
    # driver B is paid exactly once, driver A never
    driver_ops = [p for p in rt.ledger.db.execute("SELECT operation_id FROM payments WHERE mission_id=?", (mid,)).fetchall()
                  if "driver" in p[0]]
    assert len(driver_ops) == 1
    assert [m["kind"] for m in r.run("coverage_replan", "c1", [])["money"]].count("pay_driver") == 1
    es = rt.audit.query(mission_id=mid, limit=500)
    seq = {k: min(e["seq"] for e in es if e["kind"] == k)
           for k in ("input.event", "plan.refused", "fuel.revoke", "replan", "assignment.change")}
    assert seq["input.event"] < seq["plan.refused"] < seq["fuel.revoke"] < seq["replan"] < seq["assignment.change"]
    assert all(e["mission_id"] == mid for e in es)
    refused = next(e for e in es if e["kind"] == "plan.refused")
    assert refused["party"] == "driver" and refused["payload"]["driver_id"] == "drv_1" and refused["payload"]["reasons"]
    assert next(e for e in es if e["kind"] == "assignment.change")["payload"]["new_driver"] == "drv_2"
    assert next(e for e in es if e["kind"] == "fuel.revoke")["payload"]["driver_id"] == "drv_1"
    pay = next(e for e in es if e["kind"] == "stripe.call" and "pay_driver" in e["summary"])
    assert pay["seq"] > seq["assignment.change"]
    assert rt.ledger.mission(mid)["revision"] >= 1


def test_coverage_replan_no_compliant_candidate_escalates_without_weakening():
    rt, r = make()
    v = r.run("coverage_replan_no_candidate", "c2")
    assert v["status"] == "escalated" and kinds(v) == ["collect"]
    replans = rt.audit.query(mission_id=v["mission_id"], kind="replan")
    assert len(replans) == 2
    assert replans[0]["payload"]["new_plan"]["driver_id"] == "drv_2"
    assert replans[1]["payload"]["new_plan"]["status"] == "refused"
    assert all(e["payload"]["policy_weakened"] is False for e in replans)
    assert rt.audit.query(mission_id=v["mission_id"], kind="plan.refused")
    assert rt.ledger.mission(v["mission_id"])["status"] == "escalated"


def test_cancel_refund_scenario():
    gw = MockGateway()
    rt, r = make(gw)
    v = r.run("cancel_refund", "r1")
    assert v["status"] == "completed"
    assert kinds(v) == ["collect", "refund", "collect"]        # late cancel collected but never refunded
    refund = v["money"][1]
    assert refund["amount_minor"] == 4500 and refund["livemode"] is False
    assert v["refund_id"] == refund["provider_ref"]
    assert [p["outcome"] for p in v["phases"]] == ["refunded", "refused_escalated"]
    assert [st.get("status") for st in v["steps"] if st.get("phase") == "primary_cancel_before_pickup"][-1] \
        == "duplicate_ignored"
    entries = rt.audit.query(mission_id=v["mission_id"], limit=2000)
    dups = [e for e in entries if "duplicate_ignored" in e["summary"]]
    assert len(dups) == 1 and dups[0]["mission_id"] == v["mission_id"] and dups[0]["party"] == "sender"
    assert len([k for k in gw._results if k[0] == "refund"]) == 1                                 # exactly one refund object, no double money
    assert ("system", "escalation") in audit_kinds(rt, v["mission_id"] + "-late")
    assert rt.audit.verify()["ok"]


def test_all_scenarios_registered():
    assert set(SCENARIO_IDS) == {"deliver_and_release", "fuel_entitlement", "coverage_replan", "cancel_refund"}


def test_routes():
    app = FastAPI()
    app.include_router(routes_agent.router, prefix="/api")
    routes_agent.configure(Ledger(":memory:"), MockGateway(), default_registry(), from_env=False)
    c = TestClient(app)
    st = c.get("/api/agent/status").json()
    assert st["agent_mode"] == "goap_only" and st["llm"]["note"] == GOAP_ONLY_NOTE
    assert c.post("/api/scenarios/bogus/run").status_code == 404
    assert c.post("/api/agent/step", json={"run_id": "zzz"}).status_code == 404
    v = c.post("/api/scenarios/deliver_and_release/run", json={"run_id": "x"}).json()
    assert v["status"] == "awaiting_approval"
    s = c.post("/api/agent/step", json={"run_id": "x"}).json()
    assert s["label"] == "simulated" and s["run"]["status"] == "awaiting_approval"
    assert c.post("/api/scenarios/deliver_and_release/run", json={"run_id": "y", "approvals": ["merchant"]}).status_code == 409
    done = c.post("/api/scenarios/deliver_and_release/run", json={"run_id": "x", "approvals": ["merchant"]}).json()
    assert done["status"] == "completed"
    assert c.get("/api/scenarios/deliver_and_release/runs/x").json()["status"] == "completed"
    assert c.get("/api/scenarios/fuel_entitlement/runs/x").status_code == 404
