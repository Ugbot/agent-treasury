from types import SimpleNamespace

import pytest

from treasury import planner as pl
from treasury.agent import (
    TOOLS,
    Agent,
    AgentContext,
    PlannerSession,
    Proposal,
    plan_gate,
    registry_policy_check,
)
from treasury.audit import AuditLog
from treasury.geo import Point
from treasury.ledger import Ledger
from treasury.policies import default_registry
from treasury.policy import Clause

N = pl.Zone("N", (Point(51.5, -0.12), Point(51.5, -0.08), Point(51.51, -0.08), Point(51.51, -0.12)), "permitted")
R1 = pl.Route("R1", (Point(51.502, -0.11), Point(51.508, -0.09)))
JOB = pl.Job(value_minor=3000, expected_duration_s=3600, merchant_minor=2500, driver_fee_minor=700)


def cov(d, until=1e9):
    return pl.Coverage(d, ("parcel",), ("van",), 50_000, ("N",), until)


class FakeLLM:
    """Stands in for anthropic.Anthropic: returns scripted tool_use blocks."""

    def __init__(self, *calls, error=None):
        self.calls, self.error, self.seen = list(calls), error, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.seen.append(kw)
        if self.error:
            raise self.error
        name, inp = self.calls.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name=name, input=inp)])


def setup(receipt=True, drivers=("A",), coverage=None, **inputs):
    led = Ledger(":memory:")
    led.create_mission("m1", 3200, "USD")
    led.apply_event("e1", "m1", 0, {"receipt_confirmed": receipt, **inputs})
    audit = AuditLog(led.db)
    world = pl.World(JOB, tuple(pl.Driver(d) for d in drivers),
                     tuple(coverage if coverage is not None else [cov(d) for d in drivers]), (N,), (R1,))
    session = PlannerSession(audit, "m1", world)
    m = led.mission("m1")
    import json
    ctx = AgentContext("m1", "usd", json.loads(m["inputs"]), m["revision"], session)
    return led, audit, session, ctx


def agent_with(audit, client=None, executed=None, **kw):
    ex = (lambda p, c, v: executed.append(p.kind) or {"state": "confirmed"}) if executed is not None else None
    return Agent(audit, default_registry(), client=client, executor=ex, **kw)


def kinds(audit, kind=None):
    return [(e["actor"], e["kind"]) for e in audit.query(mission_id="m1") if kind is None or e["kind"] == kind]


def test_goap_only_mode_is_explicit_and_executes_allowed_step():
    _, audit, _, ctx = setup()
    done = []
    a = agent_with(audit, executed=done)
    assert a.status()["llm"]["available"] is False
    assert "GOAP-only" in a.status()["llm"]["note"]
    r = a.step(ctx)
    assert r.source == "goap" and r.agent_mode == "goap_only"
    assert r.status == "approved" and done == [r.proposal["kind"]]
    assert ("agent", "agent.proposal") in kinds(audit) and ("planner", "agent.verdict") in kinds(audit)
    assert audit.verify()["ok"]


def test_policy_hold_is_recorded_and_never_executed():
    _, audit, _, ctx = setup(receipt=False)
    done = []
    r = agent_with(audit, executed=done).step(ctx)
    assert r.status == "held" and not r.executed and done == []
    v = audit.query(mission_id="m1", kind="agent.verdict")[-1]
    assert v["policy"]["verdict"] == "hold"
    assert any(c["clause"] == "PAY-1" and c["outcome"] == "hold" for c in v["policy"]["clauses"])


def test_prohibited_zone_denies():
    _, audit, _, ctx = setup(prohibited_zone=True)
    done = []
    r = agent_with(audit, executed=done).step(ctx)
    assert r.status == "denied" and done == []


def test_llm_proposal_goes_through_policy_and_plan():
    _, audit, _, ctx = setup()
    llm = FakeLLM(("pay_driver", {"amount_minor": 700, "driver_id": "A", "reason": "plan step"}))
    done = []
    r = agent_with(audit, client=llm, executed=done).step(ctx)
    assert r.source == "llm" and r.agent_mode == "llm+goap" and r.status == "approved" and done == ["pay_driver"]
    sent = llm.seen[0]
    assert sent["model"] == "claude-sonnet-5-5" and sent["tool_choice"] == {"type": "auto"}
    assert {t["name"] for t in sent["tools"]} == {t["name"] for t in TOOLS} and len(TOOLS) == 7


def test_llm_amount_outside_plan_is_blocked():
    _, audit, _, ctx = setup()
    llm = FakeLLM(("pay_merchant", {"amount_minor": 99_000, "reason": "bigger"}))
    done = []
    r = agent_with(audit, client=llm, executed=done).step(ctx)
    assert r.status == "blocked_by_plan" and done == []
    assert "plan entitlement" in r.plan_gate["reason"]


def test_invalid_llm_proposal_is_rejected_by_schema():
    _, audit, _, ctx = setup()
    llm = FakeLLM(("pay_driver", {"amount_minor": -5, "reason": "x"}))
    done = []
    r = agent_with(audit, client=llm, executed=done).step(ctx)
    assert r.status == "invalid" and done == []
    assert ("agent", "agent.invalid_proposal") in kinds(audit)
    with pytest.raises(ValueError):
        Proposal(kind="pay_driver", amount_minor=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Proposal(kind="delete_everything")  # type: ignore[arg-type]


def test_llm_outage_falls_back_to_goap_and_says_so():
    _, audit, _, ctx = setup()
    r = agent_with(audit, client=FakeLLM(error=RuntimeError("down"))).step(ctx)
    assert r.source == "goap_fallback"
    assert ("agent", "agent.llm_error") in kinds(audit)


def test_wrong_currency_is_denied_never_executed():
    _, audit, _, ctx = setup()
    llm = FakeLLM(("pay_driver", {"amount_minor": 700, "driver_id": "A", "currency": "eur", "reason": "x"}))
    done = []
    r = agent_with(audit, client=llm, executed=done).step(ctx)
    assert r.status == "denied" and done == []


def test_stale_driver_blocked_by_plan_gate():
    _, _, _, ctx = setup(drivers=("A", "B"))
    p = Proposal(kind="pay_driver", amount_minor=700, driver_id="Z")
    ok, why = plan_gate(p, ctx)
    assert not ok and "not the assigned driver" in why


def test_no_plan_only_escalates_and_never_weakens_policy():
    _, audit, s, ctx = setup(coverage=[cov("A", until=1.0)])
    assert s.plan.status == "refused"
    done = []
    llm = FakeLLM(("pay_driver", {"amount_minor": 700, "driver_id": "A", "reason": "try"}))
    r = agent_with(audit, client=llm, executed=done).step(ctx)
    assert r.status == "blocked_by_plan" and done == []
    r2 = agent_with(audit, executed=done).step(ctx)  # GOAP escalates
    assert r2.proposal["kind"] == "escalate" and done == ["escalate"]


def test_approval_only_hold_needs_bound_approval():
    job = pl.Job(value_minor=3000, merchant_minor=7000, driver_fee_minor=700)
    _, audit, s, ctx = setup()
    s.world = pl.World(job, s.world.drivers, s.world.coverage, s.world.zones, s.world.routes)
    s.plan = pl.make_plan(s.world)
    done = []
    agent_with(audit, executed=done).step(ctx)  # pay_driver first (allowed)
    s.complete("pay_driver")
    r = agent_with(audit, executed=done).step(ctx)
    assert r.status == "held" and r.approval_request["amount_minor"] == 7000 and done == ["pay_driver"]
    approve = []
    r = agent_with(audit, executed=done, approval_check=lambda p, c, v: approve.append(v.effective_hash) or True).step(ctx)
    assert r.status == "approved" and done == ["pay_driver", "pay_merchant"]
    assert ("approver", "approval") in kinds(audit)


def test_attached_stricter_policy_flips_allow_to_hold():
    _, audit, _, ctx = setup()
    reg = default_registry()
    reg.publish("strict", "Tight cap", [Clause("CAP-1", "Max 100", "max_amount", {"limit_minor": 100})])
    reg.attach("mission", "m1", "strict", 1, 0)
    a = Agent(audit, policy_check=registry_policy_check(reg))
    r = a.step(ctx)
    assert r.status == "held" and any(c["clause"] == "CAP-1" for c in r.policy["clauses"])


def test_replan_is_audited_old_vs_new_and_never_weakens():
    _, audit, s, _ = setup(drivers=("A", "B"))
    first = s.plan.driver_id
    s.apply("coverage_changed", {"coverage": cov(first, until=1.0)})
    e = audit.query(mission_id="m1", kind="replan")[-1]
    assert e["payload"]["old_plan"]["driver_id"] == first
    assert e["payload"]["new_plan"]["driver_id"] != first
    assert e["payload"]["policy_weakened"] is False
    assert "COVER-1" in e["payload"]["change"]["cause_clauses"]
    s.apply("coverage_changed", {"coverage": cov("B", until=1.0)})
    assert s.plan.status == "refused" and kinds(audit, "plan.refused")
