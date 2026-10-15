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
    kind: Literal["max_amount", "require_input", "forbid_input"]
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


def evaluate(policy: Policy, inputs: dict, revision: int, amount_minor: int) -> Decision:
    """Deny beats hold beats allow (restrictive combination)."""
    results, verdict = [], "allow"
    rank = {"allow": 0, "hold": 1, "deny": 2}
    for c in policy.clauses:
        outcome, evidence = "allow", {}
        if c.kind == "max_amount":
            limit = c.params["limit_minor"]
            if amount_minor > limit:
                outcome, evidence = "hold", {"amount_minor": amount_minor, "limit_minor": limit}
        elif c.kind == "require_input":
            key = c.params["key"]
            if not inputs.get(key):
                outcome, evidence = "hold", {"missing": key}
        elif c.kind == "forbid_input":
            key = c.params["key"]
            if inputs.get(key):
                outcome, evidence = "deny", {"present": key}
        results.append({"clause": c.id, "outcome": outcome, "evidence": evidence})
        if rank[outcome] > rank[verdict]:
            verdict = outcome
    return Decision(verdict, policy.id, policy.version, policy.hash, revision, tuple(results))


DEFAULT_POLICY = Policy(
    id="org-base",
    version=1,
    clauses=(
        Clause("PAY-1", "Release only after delivery proof", "require_input", {"key": "receipt_confirmed"}),
        Clause("TRAVEL-1", "Never pay when route enters a prohibited zone", "forbid_input", {"key": "prohibited_zone"}),
        Clause("APPROVE-1", "Amounts above GBP 50.00 need approval", "max_amount", {"limit_minor": 5000}),
    ),
)
