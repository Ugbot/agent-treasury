"""The agent: proposes the NEXT action only. It has no payment authority.

Every proposal (from the LLM, or from the GOAP planner when no ANTHROPIC_API_KEY is set) is
1. recorded in the audit log (actor 'agent'),
2. checked against the compliant GOAP plan and against the effective policy (injected callable;
   default = PolicyRegistry.decide), and the verdict is recorded (actor 'planner'),
3. handed to an injected executor ONLY when the verdict is allow (or a bound human approval
   covers an approval-only hold). Anything else is recorded and never executed.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import planner as pl
from .audit import AuditLog
from .policies import PolicyRegistry
from .stripe_gateway import audit_scope

MODEL = "claude-sonnet-5-5"
KINDS = ("pay_merchant", "pay_driver", "issue_fuel_allowance", "request_approval",
         "refund", "replan", "escalate")
MONEY_KINDS = ("pay_merchant", "pay_driver", "issue_fuel_allowance", "refund")
GOAP_ONLY_NOTE = "ANTHROPIC_API_KEY not set: running in GOAP-only mode (no LLM proposals)."

# Clauses that do not apply to a money-in or pre-delivery action. Narrow, explicit, and written
# into the audit entry as `waived`, never silent. PAY-1 governs release of a delivery payment.
WAIVERS: dict[str, dict[str, str]] = {
    "issue_fuel_allowance": {"PAY-1": "pre-delivery allowance, capped by the fuel policy; no delivery payment released"},
    "refund": {"PAY-1": "refund returns money to the payer; it is not a release of delivery payment"},
}


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["pay_merchant", "pay_driver", "issue_fuel_allowance", "request_approval",
                  "refund", "replan", "escalate"]
    amount_minor: int | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    driver_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=400)

    def to_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


_PROPS = {
    "amount_minor": {"type": "integer", "minimum": 1, "description": "Integer minor units (never decimals)."},
    "currency": {"type": "string", "description": "Must equal the mission currency; omit to use it."},
    "driver_id": {"type": "string"},
    "reason": {"type": "string", "description": "Why this is the next action (short)."},
}
_DESC = {
    "pay_merchant": "Propose releasing the merchant payment leg.",
    "pay_driver": "Propose releasing the driver payment leg.",
    "issue_fuel_allowance": "Propose issuing the capped trip fuel allowance to the assigned driver.",
    "request_approval": "Ask a human approver; use when a policy hold needs a person.",
    "refund": "Propose refunding the payer after cancellation (omit amount for the full refundable).",
    "replan": "Ask the planner to recompute the plan because inputs changed.",
    "escalate": "Hand the mission to a human; use when no compliant plan exists.",
}
TOOLS = [{"name": k, "description": _DESC[k],
          "input_schema": {"type": "object", "properties": _PROPS, "required": ["reason"],
                           "additionalProperties": False}} for k in KINDS]

SYSTEM = (
    "You are the treasury agent for a delivery mission. You can only PROPOSE the single next action "
    "by calling exactly one tool. You have no payment authority: the planner and the written policy "
    "check every proposal and anything not allowed is never executed. Follow the compliant plan "
    "provided; never propose amounts outside it; if the plan is refused, escalate. Amounts are "
    "integer minor units in the mission currency."
)


# ----------------------------------------------------------------------------- planner session

def plan_dict(plan: pl.Plan) -> dict:
    return {"status": plan.status, "steps": list(plan.step_names), "completed": list(plan.completed),
            "driver_id": plan.driver_id, "route_id": plan.route_id,
            "assignment_version": plan.assignment_version, "total_cost": plan.total_cost,
            "entitlements": [dict(e) for e in plan.entitlements], "reasons": [dict(r) for r in plan.reasons],
            "resolution": plan.resolution, "policy_weakened": plan.policy_weakened,
            "world_revision": plan.world_revision}


def change_dict(c: pl.PlanChange) -> dict:
    return {"old_steps": list(c.old_steps), "new_steps": list(c.new_steps), "removed": list(c.removed),
            "added": list(c.added), "kept_completed": list(c.kept_completed),
            "cause_clauses": list(c.cause_clauses), "causes": [dict(x) for x in c.causes]}


KIND_PARTY = {"pay_merchant": "receiver", "pay_driver": "driver", "issue_fuel_allowance": "driver",
              "refund": "sender", "escalate": "sender", "request_approval": "approver"}


def party_for(kind: str | None) -> str:
    """Audit party for an action kind: whose money/record it concerns (per-party logs)."""
    return KIND_PARTY.get(kind or "", "sender")


class PlannerSession:
    """GOAP planner bound to one mission; every plan and replan is audited (old vs new)."""

    def __init__(self, audit: AuditLog, mission_id: str, world: pl.World, mode: str = "mock"):
        self.audit, self.mission_id, self.world, self.mode = audit, mission_id, world, mode
        self.plan: pl.Plan = pl.make_plan(world)
        self.effects: set[str] = set()  # agent-level effects that are not planner actions
        self.audit.append("planner", "plan", f"plan {self.plan.status}: {' > '.join(self.plan.step_names) or '-'}",
                          mission_id=mission_id, party="sender",
                          payload={"plan": plan_dict(self.plan), "simulated": True},
                          mode=mode)

    def apply(self, kind: str, payload: dict, note: str = "") -> pl.PlanChange:
        """Apply a labelled simulated map event, replan, and audit old vs new plan."""
        old = self.plan
        self.world = pl.apply_event(self.world, pl.WorldEvent(kind, payload))  # type: ignore[arg-type]
        self.plan, change = pl.replan(old, self.world)
        self.audit.append(
            "planner", "replan", note or f"replan after {kind}: {old.status} -> {self.plan.status}",
            mission_id=self.mission_id, party="sender",
            payload={"event": kind, "old_plan": plan_dict(old), "new_plan": plan_dict(self.plan),
                     "change": change_dict(change), "policy_weakened": False, "simulated": True},
            mode=self.mode)
        if self.plan.status == "refused":
            self.audit.append("planner", "plan.refused",
                              f"no compliant plan; resolution={self.plan.resolution}: escalate, policy not weakened",
                              mission_id=self.mission_id, party="sender",
                              payload={"reasons": [dict(r) for r in self.plan.reasons]}, mode=self.mode)
        return change

    def complete(self, *names: str) -> None:
        self.plan = pl.make_plan(self.world, tuple(dict.fromkeys((*self.plan.completed, *names))), self.plan)

    def replan(self) -> pl.PlanChange:
        old = self.plan
        self.plan, change = pl.replan(old, self.world)
        self.audit.append("planner", "replan", f"replan requested: {old.status} -> {self.plan.status}",
                          mission_id=self.mission_id, party="sender",
                          payload={"old_plan": plan_dict(old), "new_plan": plan_dict(self.plan),
                                   "change": change_dict(change)}, mode=self.mode)
        return change

    def entitlement(self, kind: str) -> dict | None:
        return next((e for e in self.plan.entitlements if e["type"] == kind), None)


# ----------------------------------------------------------------------------- context + policy

@dataclass
class AgentContext:
    mission_id: str
    currency: str
    inputs: dict
    revision: int
    session: PlannerSession
    facts: dict = field(default_factory=dict)       # e.g. cancelled, refund_state, focus
    pending_approvals: dict = field(default_factory=dict)


@dataclass
class PolicyVerdict:
    verdict: Literal["allow", "hold", "deny"]
    policy: dict
    reasons: list[str] = field(default_factory=list)
    needs_approval: bool = False
    effective_hash: str = ""


PolicyCheck = Callable[[Proposal, AgentContext], PolicyVerdict]
Executor = Callable[[Proposal, AgentContext, PolicyVerdict], dict]
ApprovalCheck = Callable[[Proposal, AgentContext, PolicyVerdict], bool]


def registry_policy_check(registry: PolicyRegistry) -> PolicyCheck:
    """Default: the effective-decision function (org-base + attached policies, deny > hold > allow)."""

    def check(p: Proposal, ctx: AgentContext) -> PolicyVerdict:
        if p.kind not in MONEY_KINDS:
            pol = {"id": "none", "verdict": "allow", "clauses": [],
                   "note": "non-monetary action; no funds move"}
            return PolicyVerdict("allow", pol, [])
        cur = (p.currency or ctx.currency).lower()
        if cur != ctx.currency.lower():
            pol = {"id": "CURRENCY-1", "verdict": "deny",
                   "clauses": [{"clause": "CURRENCY-1", "outcome": "deny",
                                "evidence": {"proposed": cur, "mission": ctx.currency.lower()}}]}
            return PolicyVerdict("deny", pol, [f"currency {cur} differs from mission currency"])
        amount = p.amount_minor or int(ctx.facts.get("amount_minor") or 0) or 1
        inputs = {**ctx.inputs, "currency": cur}
        d = registry.decide("payment", f"{ctx.mission_id}:{p.kind}:{amount}", inputs, ctx.revision, amount,
                            inherited=[("mission", ctx.mission_id)])
        waived = WAIVERS.get(p.kind, {})
        verdict, clauses, not_ok = "allow", [], []
        for r in d.results:
            outcome = r["outcome"]
            if outcome != "allow" and r.get("clause") in waived:
                clauses.append({**r, "outcome": "allow", "waived": waived[r["clause"]], "raw_outcome": outcome})
                continue
            clauses.append(r)
            if outcome != "allow":
                not_ok.append(r)
                verdict = "deny" if outcome == "deny" or verdict == "deny" else "hold"
        if d.reasons:
            verdict = "deny" if verdict == "deny" else "hold"
            not_ok.append({"clause": None, "reasons": list(d.reasons)})
        needs_approval = (verdict == "hold" and bool(not_ok) and not d.reasons
                          and all(str(r.get("clause") or "").startswith("APPROVE") for r in not_ok))
        pol = {"id": "+".join(sorted({a[1] for a in d.attachments})) or "none", "hash": d.effective_policy_hash,
               "verdict": verdict, "clauses": clauses, "attachments": [list(a) for a in d.attachments]}
        reasons = [f"{r['clause']}:{r['outcome']}" if r.get("clause") else "; ".join(r["reasons"]) for r in not_ok]
        return PolicyVerdict(verdict, pol, reasons, needs_approval, d.effective_policy_hash)  # type: ignore[arg-type]

    return check


# ----------------------------------------------------------------------------- plan gate

def plan_gate(p: Proposal, ctx: AgentContext) -> tuple[bool, str]:
    """The compliant plan constrains what may be proposed. Never relaxes policy."""
    plan = ctx.session.plan
    if plan.status == "refused":
        if p.kind in ("escalate", "request_approval", "replan", "refund"):
            return True, "no compliant plan: only escalation-type actions allowed"
        return False, "no_compliant_plan: " + "; ".join(
            f"{r.get('clause')}:{r.get('detail')}" for r in plan.reasons[:3])
    if p.kind in ("pay_merchant", "pay_driver"):
        if p.kind not in plan.step_names:
            return False, f"{p.kind} is not a pending step of the compliant plan"
        ent = ctx.session.entitlement("merchant_payment" if p.kind == "pay_merchant" else "driver_payment")
        if ent is None or p.amount_minor != ent["amount_minor"]:
            return False, f"amount {p.amount_minor} differs from plan entitlement {ent and ent['amount_minor']}"
        if p.kind == "pay_driver" and p.driver_id != plan.driver_id:
            return False, f"driver {p.driver_id} is not the assigned driver {plan.driver_id} (stale plan)"
    if p.kind == "issue_fuel_allowance":
        ent = ctx.session.entitlement("fuel")
        if ent is None or ent["status"] != "pending":
            return False, "no pending fuel entitlement in plan"
        if p.driver_id != ent["driver_id"]:
            return False, f"driver {p.driver_id} is not the assigned driver {ent['driver_id']}"
        if p.amount_minor is None or p.amount_minor > ent["cap_minor"]:
            return False, f"amount above planned fuel cap {ent['cap_minor']}"
    return True, "consistent with compliant plan"


# ----------------------------------------------------------------------------- proposers

def goap_proposal(ctx: AgentContext) -> Proposal | None:
    """Deterministic proposer: the next action implied by the compliant plan and mission facts."""
    s, plan, f = ctx.session, ctx.session.plan, ctx.facts
    if plan.status == "refused":
        if f.get("cancelled") and f.get("refund_state") is None:
            return Proposal(kind="refund", reason="plan refused and mission cancelled: return funds")
        if "escalated" in s.effects:
            return None
        return Proposal(kind="escalate", reason="no compliant plan exists; policy is not weakened")
    if f.get("cancelled"):
        if f.get("refund_state") is None:
            return Proposal(kind="refund", reason="mission cancelled: refund the payer")
        if f.get("refund_state") in ("held", "escalated", "unknown") and "escalated" not in s.effects:
            return Proposal(kind="escalate", reason=f"refund {f['refund_state']}: needs a person")
        return None
    fuel = s.entitlement("fuel")
    if f.get("focus") == "fuel" and fuel and fuel["status"] == "pending" and "fuel_issued" not in s.effects:
        return Proposal(kind="issue_fuel_allowance", amount_minor=fuel["cap_minor"], driver_id=fuel["driver_id"],
                        reason="assigned driver needs capped trip fuel")
    if f.get("focus") == "fuel":
        return None
    for step in plan.steps:
        if step.name in ctx.pending_approvals:
            continue  # waiting for a human; other legs may proceed
        if step.name in ("pay_merchant", "pay_driver"):
            ent = s.entitlement("merchant_payment" if step.name == "pay_merchant" else "driver_payment")
            assert ent is not None
            return Proposal(kind=step.name, amount_minor=ent["amount_minor"], driver_id=plan.driver_id,  # type: ignore[arg-type]
                            reason=f"next compliant plan step: {step.name}")
    return None


def _llm_state(ctx: AgentContext, audit: AuditLog) -> dict:
    recent = audit.query(mission_id=ctx.mission_id, limit=500)[-8:]
    return {"mission": {"id": ctx.mission_id, "currency": ctx.currency, "inputs": ctx.inputs,
                        "revision": ctx.revision},
            "plan": plan_dict(ctx.session.plan), "facts": ctx.facts,
            "pending_approvals": ctx.pending_approvals,
            "recent_audit": [{"seq": e["seq"], "actor": e["actor"], "kind": e["kind"], "summary": e["summary"]}
                             for e in recent]}


def client_from_env():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=key, timeout=30, max_retries=1)


@dataclass
class StepResult:
    status: str  # approved | held | denied | blocked_by_plan | invalid | none
    proposal: dict | None
    source: str  # llm | goap | goap_fallback | none
    policy: dict | None = None
    plan_gate: dict | None = None
    executed: bool = False
    execution: dict | None = None
    approval_request: dict | None = None
    agent_mode: str = "goap_only"
    note: str = ""
    audit_seqs: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class Agent:
    def __init__(self, audit: AuditLog, registry: PolicyRegistry | None = None, *, client: Any = None,
                 policy_check: PolicyCheck | None = None, executor: Executor | None = None,
                 approval_check: ApprovalCheck | None = None, model: str = MODEL, mode: str = "mock"):
        self.audit, self.client, self.model, self.mode = audit, client, model, mode
        if policy_check is None:
            if registry is None:
                raise ValueError("provide a registry or a policy_check")
            policy_check = registry_policy_check(registry)
        self.policy_check, self.executor, self.approval_check = policy_check, executor, approval_check

    @property
    def agent_mode(self) -> str:
        return "llm+goap" if self.client is not None else "goap_only"

    def status(self) -> dict:
        return {"agent_mode": self.agent_mode, "model": self.model if self.client else None,
                "llm": {"available": self.client is not None,
                        "note": "" if self.client else GOAP_ONLY_NOTE},
                "authority": "none: proposals are policy-checked; only allowed ones reach the executor"}

    # --- proposing ---
    def _llm_propose(self, ctx: AgentContext) -> Proposal | None:
        resp = self.client.messages.create(  # type: ignore[union-attr]
            model=self.model, max_tokens=800, system=SYSTEM, tools=TOOLS, tool_choice={"type": "auto"},  # this model rejects forced tool choice; SYSTEM says to always call a tool
            messages=[{"role": "user", "content": "State:\n" + json.dumps(_llm_state(ctx, self.audit), default=str)
                       + "\nPropose the single next action by calling exactly one tool."}])
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                return Proposal(kind=block.name, **dict(block.input))
        raise ValueError("model returned no tool call")

    def propose(self, ctx: AgentContext, use_llm: bool = True) -> tuple[Proposal | None, str, str]:
        if self.client is not None and use_llm:
            try:
                return self._llm_propose(ctx), "llm", ""
            except (ValidationError, ValueError, TypeError) as e:
                self.audit.append("agent", "agent.invalid_proposal", f"LLM proposal rejected by schema: {str(e)[:160]}",
                                  mission_id=ctx.mission_id, party="sender", mode=self.mode)
                return None, "llm", "invalid"
            except Exception as e:  # noqa: BLE001 - any provider failure: explicit, recorded fallback
                self.audit.append("agent", "agent.llm_error",
                                  f"LLM unavailable ({type(e).__name__}); falling back to GOAP proposal",
                                  mission_id=ctx.mission_id, party="sender", mode=self.mode)
                return goap_proposal(ctx), "goap_fallback", ""
        return goap_proposal(ctx), "goap", ""

    # --- the step ---
    def step(self, ctx: AgentContext, use_llm: bool = True) -> StepResult:
        mid, base = ctx.mission_id, {"agent_mode": self.agent_mode}
        prop, source, flag = self.propose(ctx, use_llm)
        if prop is None:
            note = "model proposal invalid; nothing executed" if flag == "invalid" else "no further action"
            return StepResult("invalid" if flag == "invalid" else "none", None, source, note=note, **base)
        if prop.currency is None and prop.kind in MONEY_KINDS:
            prop = prop.model_copy(update={"currency": ctx.currency})
        seqs = [self.audit.append(
            "agent", "agent.proposal", f"{source} proposes {prop.kind}"
            + (f" {prop.amount_minor} {prop.currency}" if prop.amount_minor else ""),
            mission_id=mid, party=party_for(prop.kind),
            payload={"proposal": prop.to_dict(), "source": source, "agent_mode": self.agent_mode,
                                     "note": "" if self.client else GOAP_ONLY_NOTE}, mode=self.mode)]
        ok, why = plan_gate(prop, ctx)
        verdict = self.policy_check(prop, ctx) if ok else PolicyVerdict(
            "deny", {"id": "plan-gate", "verdict": "deny", "clauses": []}, [why])
        approved_by_human = False
        if ok and verdict.verdict == "hold" and verdict.needs_approval and self.approval_check \
                and self.approval_check(prop, ctx, verdict):
            approved_by_human = True
            seqs.append(self.audit.append(
                "approver", "approval", f"human approval consumed for {prop.kind} {prop.amount_minor}",
                mission_id=mid, party="approver",
                payload={"proposal": prop.to_dict(), "bound_to_policy_hash": verdict.effective_hash},
                policy=verdict.policy, mode=self.mode))
        effective = "allow" if approved_by_human else verdict.verdict
        status = ("blocked_by_plan" if not ok else
                  "approved" if effective == "allow" else "held" if verdict.verdict == "hold" else "denied")
        seqs.append(self.audit.append(
            "planner", "agent.verdict",
            f"{prop.kind}: plan_gate={'pass' if ok else 'fail'}, policy={verdict.verdict}"
            + (" (human approved)" if approved_by_human else "") + f" -> {status}",
            mission_id=mid, party=party_for(prop.kind),
            payload={"proposal": prop.to_dict(), "plan_gate": {"ok": ok, "reason": why}, "status": status,
                     "reasons": verdict.reasons, "executed": status == "approved" and self.executor is not None,
                     "human_approved": approved_by_human},
            policy={**verdict.policy, "verdict": verdict.verdict}, mode=self.mode))
        res = StepResult(status, prop.to_dict(), source, verdict.policy, {"ok": ok, "reason": why},
                         audit_seqs=seqs, note=why if not ok else "", **base)
        if status == "held" and verdict.needs_approval:
            res.approval_request = {"kind": prop.kind, "amount_minor": prop.amount_minor, "currency": prop.currency,
                                    "policy_hash": verdict.effective_hash, "reasons": verdict.reasons}
        if status == "approved" and self.executor is not None:
            with audit_scope(mid, party_for(prop.kind)):
                res.execution = self.executor(prop, ctx, verdict)
            res.executed = True
        return res
