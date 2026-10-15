"""Deterministic, versioned policy evaluation. Pure functions; no I/O."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["allow", "hold", "deny"]


@dataclass(frozen=True)
class Clause:
    id: str
    prose: str
    # typed rule data; kind selects the check
    kind: Literal["max_amount", "require_input", "forbid_input", "allowed_currency",
                  "allowed_counterparties", "velocity_limit", "payment_freeze"]
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Policy:
    id: str
    version: int
    clauses: tuple[Clause, ...]

    @property
    def hash(self) -> str:
        body = json.dumps(
            [self.id, self.version, [(c.id, c.kind, c.params) for c in self.clauses]],
            sort_keys=True,
        )
        return hashlib.sha256(body.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    policy_id: str
    policy_version: int
    policy_hash: str
    input_revision: int
    results: tuple[dict, ...]  # per clause: id, outcome, evidence


def _check_counterparties(params: dict, inputs: dict) -> tuple[str, dict]:
    """Recipient / merchant-category allow lists. Facts are server-owned; missing -> hold."""
    for field_, key in (("recipients", "recipient"), ("categories", "merchant_category")):
        allowed = params.get(field_) or []
        if not allowed:
            continue
        if key == "merchant_category" and inputs.get("category_required") is False:
            continue  # payment path supplies no merchant category (mission payments): recipient-only check
        v = str(inputs.get(key) or "").strip().lower()
        if not v:
            return "hold", {"missing": key}
        if v not in [str(a).strip().lower() for a in allowed]:
            return "deny", {key: v, "allowed_" + field_: list(allowed)}
    return "allow", {}


def _check_velocity(params: dict, inputs: dict) -> tuple[str, dict]:
    """Confirmed payments (server-supplied epoch times) in the trailing window ending at inputs['now']."""
    limit, window = params["max_payments"], params["window_seconds"]
    now, times = inputs.get("now"), inputs.get("payment_times")
    if not isinstance(now, (int, float)) or isinstance(now, bool) or not isinstance(times, list):
        return "hold", {"missing": "payment_history"}
    n = sum(1 for t in times if isinstance(t, (int, float)) and now - window < t <= now)
    if n >= limit:
        return "hold", {"payments_in_window": n, "max_payments": limit, "window_seconds": window}
    return "allow", {}


def evaluate(policy: Policy, inputs: dict, revision: int, amount_minor: int) -> Decision:
    """Deny beats hold beats allow (restrictive combination)."""
    results, verdict = [], "allow"
    rank = {"allow": 0, "hold": 1, "deny": 2}
    for c in policy.clauses:
        outcome, evidence = "allow", {}
        if c.kind == "max_amount":
            limit = c.params["limit_minor"]
            if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
                outcome, evidence = "deny", {"invalid_type": type(amount_minor).__name__, "field": "amount_minor"}
            elif amount_minor > limit:
                outcome, evidence = "hold", {"amount_minor": amount_minor, "limit_minor": limit}
        elif c.kind == "require_input":
            # Strict: only the boolean True satisfies a requirement ("false", 1, "yes" do not).
            key = c.params["key"]
            v = inputs.get(key)
            if v is not True:
                outcome = "hold"
                evidence = {"missing": key}
                if v is not None and v is not False:
                    evidence["invalid_type"] = type(v).__name__
        elif c.kind == "forbid_input":
            # Fail closed: anything other than absent/None/False counts as present.
            key = c.params["key"]
            v = inputs.get(key)
            if v is not None and v is not False:
                outcome, evidence = "deny", {"present": key}
                if v is not True:
                    evidence["invalid_type"] = type(v).__name__
        elif c.kind == "allowed_currency":
            allowed = c.params["currencies"]
            cur = str(inputs.get("currency") or "").lower()
            if not cur:
                outcome, evidence = "hold", {"missing": "currency"}
            elif cur not in allowed:
                outcome, evidence = "deny", {"currency": cur, "allowed": list(allowed)}
        elif c.kind == "allowed_counterparties":
            outcome, evidence = _check_counterparties(c.params, inputs)
        elif c.kind == "velocity_limit":
            outcome, evidence = _check_velocity(c.params, inputs)
        elif c.kind == "payment_freeze":
            if c.params.get("active") is True:
                outcome, evidence = "hold", {"frozen": True, "reason": "payments are frozen"}
        results.append({"clause": c.id, "outcome": outcome, "evidence": evidence})
        if rank[outcome] > rank[verdict]:
            verdict = outcome
    return Decision(verdict, policy.id, policy.version, policy.hash, revision, tuple(results))


def default_currency(policy: Policy) -> str | None:
    """Settlement currency a policy implies: first entry of its allowed_currency clause."""
    for c in policy.clauses:
        if c.kind == "allowed_currency":
            return c.params["currencies"][0]
    return None


DEFAULT_POLICY = Policy(
    id="org-base",
    version=1,
    clauses=(
        Clause("CURRENCY-1", "Only settle in the organisation's permitted currencies", "allowed_currency",
               {"currencies": ["usd"]}),
        Clause("PAY-1", "Release only after delivery proof", "require_input", {"key": "receipt_confirmed"}),
        Clause("TRAVEL-1", "Never pay when route enters a prohibited zone", "forbid_input", {"key": "prohibited_zone"}),
        Clause("APPROVE-1", "Amounts above USD 50.00 need approval", "max_amount", {"limit_minor": 5000}),
    ),
)
