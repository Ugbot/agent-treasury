"""Versioned policy bundles, attachments and restrictive effective-policy decisions."""
from __future__ import annotations

import contextvars
import hashlib
import itertools
import json
import threading
from dataclasses import dataclass

from .memory import MemoryWrite, PolicyMemory, UnconfiguredMemory
from .policy import Clause, Decision, Policy, evaluate

TARGET_TYPES = ("organisation", "mission", "payment", "job", "card", "counterparty")
RANK = {"allow": 0, "hold": 1, "deny": 2}
ORG = ("organisation", "org")


class PolicyError(Exception):
    pass


class ConflictError(PolicyError):
    """Stale expected version (HTTP 409)."""


class MandatoryError(PolicyError):
    """Mandatory org rule cannot be detached or weakened (HTTP 403)."""


class NotFoundError(PolicyError):
    pass


class TargetLockedError(PolicyError):
    """Target is no longer pending (HTTP 409)."""


def content_hash(policy_id: str, version: int, title: str, clauses) -> str:
    body = json.dumps(
        [policy_id, version, title, [(c.id, c.prose, c.kind, c.params) for c in clauses]],
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class PolicyBundle:
    policy_id: str
    version: int
    title: str
    clauses: tuple[Clause, ...]
    content_hash: str
    memory: MemoryWrite

    def as_policy(self) -> Policy:
        return Policy(self.policy_id, self.version, self.clauses)

    def verify(self) -> bool:
        return content_hash(self.policy_id, self.version, self.title, self.clauses) == self.content_hash

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id, "version": self.version, "title": self.title,
            "content_hash": self.content_hash,
            "clauses": [{"id": c.id, "prose": c.prose, "kind": c.kind, "params": c.params} for c in self.clauses],
            "memory": {"ok": self.memory.ok, "status": self.memory.status,
                       "message": self.memory.message, "source_ref": self.memory.source_ref},
        }


@dataclass(frozen=True)
class Attachment:
    attachment_id: str
    target_type: str
    target_id: str
    policy_id: str
    policy_version: int
    mandatory: bool


@dataclass(frozen=True)
class EffectiveDecision:
    """Immutable record of one decision."""
    target_type: str
    target_id: str
    verdict: str
    input_revision: int
    effective_policy_hash: str
    attachments: tuple[tuple[str, str, int, bool], ...]  # attachment_id, policy_id, version, mandatory
    results: tuple[dict, ...]  # clause results with policy_id; includes pause reasons
    reasons: tuple[str, ...]


# Set only by routes_freeze while an approver toggles org-freeze.
ALLOW_FREEZE_TOGGLE: contextvars.ContextVar[bool] = contextvars.ContextVar("allow_freeze_toggle", default=False)


def _check_clauses(clauses) -> tuple[Clause, ...]:
    clauses = tuple(clauses)
    if not clauses:
        raise PolicyError("policy needs at least one clause")
    seen = set()
    for c in clauses:
        if c.id in seen:
            raise PolicyError(f"duplicate clause id {c.id}")
        seen.add(c.id)
        if c.kind in ("max_amount",):
            lim = c.params.get("limit_minor")
            if not isinstance(lim, int) or isinstance(lim, bool) or lim < 0:
                raise PolicyError(f"{c.id}: limit_minor must be non-negative integer minor units")
        elif c.kind in ("require_input", "forbid_input"):
            if not isinstance(c.params.get("key"), str) or not c.params["key"]:
                raise PolicyError(f"{c.id}: key required")
        elif c.kind == "allowed_currency":
            cur = c.params.get("currencies")
            if (not isinstance(cur, list) or not cur
                    or not all(isinstance(x, str) and len(x) == 3 and x.islower() for x in cur)):
                raise PolicyError(f"{c.id}: currencies must be a non-empty list of lowercase ISO codes")
        elif c.kind == "allowed_counterparties":
            r, k = c.params.get("recipients", []), c.params.get("categories", [])
            if (not isinstance(r, list) or not isinstance(k, list) or not (r or k)
                    or not all(isinstance(x, str) and x.strip() for x in [*r, *k])):
                raise PolicyError(f"{c.id}: recipients/categories must be lists of non-empty strings, at least one non-empty")
        elif c.kind == "velocity_limit":
            for f in ("max_payments", "window_seconds"):
                v = c.params.get(f)
                if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                    raise PolicyError(f"{c.id}: {f} must be an integer >= 1")
        elif c.kind == "payment_freeze":
            if not isinstance(c.params.get("active"), bool):
                raise PolicyError(f"{c.id}: active must be true or false")
        else:
            raise PolicyError(f"{c.id}: unknown kind {c.kind}")
    return clauses


def _weakens(old: tuple[Clause, ...], new: tuple[Clause, ...]) -> str | None:
    by_id = {c.id: c for c in new}
    for o in old:
        n = by_id.get(o.id)
        if n is None:
            return f"clause {o.id} removed"
        if n.kind != o.kind:
            return f"clause {o.id} changed kind"
        if o.kind == "max_amount" and n.params["limit_minor"] > o.params["limit_minor"]:
            return f"clause {o.id} limit raised"
        if o.kind == "allowed_currency" and not set(n.params["currencies"]) <= set(o.params["currencies"]):
            return f"clause {o.id} currencies widened"
        if o.kind == "allowed_counterparties":
            for f in ("recipients", "categories"):
                old_l = {str(x).lower() for x in o.params.get(f, [])}
                new_l = {str(x).lower() for x in n.params.get(f, [])}
                if (old_l and not new_l) or not new_l <= old_l:
                    return f"clause {o.id} {f} widened"
        if o.kind == "velocity_limit":
            if n.params["max_payments"] > o.params["max_payments"]:
                return f"clause {o.id} max_payments raised"
            if n.params["window_seconds"] < o.params["window_seconds"]:
                return f"clause {o.id} window shortened"
        if o.kind == "payment_freeze" and o.params["active"] and not n.params["active"] and not ALLOW_FREEZE_TOGGLE.get():
            return f"clause {o.id} unfreeze only via the approver freeze endpoint"
        if o.kind in ("require_input", "forbid_input") and n.params.get("key") != o.params.get("key"):
            return f"clause {o.id} key changed"
    return None


class PolicyRegistry:
    def __init__(self, memory: PolicyMemory | None = None):
        self.memory = memory or UnconfiguredMemory()
        self._lock = threading.RLock()
        self._versions: dict[str, list[PolicyBundle]] = {}
        self._attach: dict[str, Attachment] = {}
        self._target_version: dict[tuple[str, str], int] = {}
        self._locked: set[tuple[str, str]] = set()
        self._decisions: dict[tuple, EffectiveDecision] = {}
        self._seq = itertools.count(1)
        self.history: list[dict] = []

    # --- bundles -------------------------------------------------------
    def publish(self, policy_id: str, title: str, clauses, expected_version: int = 0) -> PolicyBundle:
        """expected_version is the current latest (0 for new); stale -> ConflictError."""
        with self._lock:
            cur = self._versions.get(policy_id, [])
            if expected_version != len(cur):
                raise ConflictError(f"stale: expected {expected_version}, current {len(cur)}")
            cl = _check_clauses(clauses)
            version = len(cur) + 1
            h = content_hash(policy_id, version, title, cl)
            text = f"{policy_id} v{version} {title}\n" + "\n".join(f"{c.id}: {c.prose}" for c in cl)
            mem = self.memory.write_policy(policy_id, version, h, text)
            b = PolicyBundle(policy_id, version, title, cl, h, mem)
            self._versions.setdefault(policy_id, []).append(b)
            self.history.append({"event": "publish", "policy_id": policy_id, "version": version})
            return b

    def get(self, policy_id: str, version: int | None = None) -> PolicyBundle:
        with self._lock:
            vs = self._versions.get(policy_id)
            if not vs:
                raise NotFoundError(policy_id)
            if version is None:
                return vs[-1]
            if not 1 <= version <= len(vs):
                raise NotFoundError(f"{policy_id} v{version}")
            return vs[version - 1]

    def list(self) -> list[PolicyBundle]:
        with self._lock:
            return [v[-1] for v in self._versions.values() if v]

    # --- attachments ---------------------------------------------------
    def target_version(self, target_type: str, target_id: str) -> int:
        return self._target_version.get((target_type, target_id), 0)

    def lock_target(self, target_type: str, target_id: str) -> None:
        """Mark a target no longer pending (e.g. payment executed)."""
        self._locked.add((target_type, target_id))

    def attach(self, target_type: str, target_id: str, policy_id: str, policy_version: int,
               expected_version: int, mandatory: bool = False) -> Attachment:
        with self._lock:
            if target_type not in TARGET_TYPES:
                raise PolicyError(f"unknown target type {target_type}")
            if mandatory and target_type != "organisation":
                raise PolicyError("only organisation attachments can be mandatory")
            if (target_type, target_id) in self._locked:
                raise TargetLockedError("target is not pending")
            if expected_version != self.target_version(target_type, target_id):
                raise ConflictError("stale target version")
            b = self.get(policy_id, policy_version)
            existing = [a for a in self._attach.values()
                        if (a.target_type, a.target_id, a.policy_id) == (target_type, target_id, policy_id)]
            for a in existing:
                if a.mandatory:
                    weak = _weakens(self.get(a.policy_id, a.policy_version).clauses, b.clauses)
                    if weak or not mandatory or policy_version < a.policy_version:
                        raise MandatoryError(weak or "mandatory policy cannot be downgraded or made optional")
                del self._attach[a.attachment_id]
            att = Attachment(f"att_{next(self._seq)}", target_type, target_id, policy_id, policy_version, mandatory)
            self._attach[att.attachment_id] = att
            self._target_version[(target_type, target_id)] = expected_version + 1
            self.history.append({"event": "attach", **att.__dict__})
            return att

    def detach(self, attachment_id: str, expected_version: int) -> int:
        with self._lock:
            a = self._attach.get(attachment_id)
            if a is None:
                raise NotFoundError(attachment_id)
            if a.mandatory:
                raise MandatoryError("mandatory organisation policy cannot be detached")
            if (a.target_type, a.target_id) in self._locked:
                raise TargetLockedError("target is not pending")
            if expected_version != self.target_version(a.target_type, a.target_id):
                raise ConflictError("stale target version")
            del self._attach[attachment_id]
            nv = expected_version + 1
            self._target_version[(a.target_type, a.target_id)] = nv
            self.history.append({"event": "detach", **a.__dict__})
            return nv

    def attachments_for(self, target_type: str, target_id: str) -> list[Attachment]:
        with self._lock:
            return [a for a in self._attach.values() if (a.target_type, a.target_id) == (target_type, target_id)]

    def effective_attachments(self, targets: list[tuple[str, str]]) -> list[Attachment]:
        """Mandatory org rules first, then inherited/direct attachments for given targets."""
        with self._lock:
            out: dict[str, Attachment] = {}
            for t in [ORG, *targets]:
                for a in self.attachments_for(*t):
                    out[a.attachment_id] = a
            return sorted(out.values(), key=lambda a: (not a.mandatory, a.attachment_id))

    # --- decisions -----------------------------------------------------
    def decide(self, target_type: str, target_id: str, inputs: dict, input_revision: int,
               amount_minor: int, inherited: list[tuple[str, str]] | None = None) -> EffectiveDecision:
        """Deny > hold > allow across all effective policies. Unknown/stale/tampered required
        policy pauses (hold). A completed decision for the same inputs is returned unchanged."""
        with self._lock:
            targets = [(target_type, target_id), *(inherited or [])]
            atts = self.effective_attachments(targets)
            sig = hashlib.sha256(
                json.dumps([(a.attachment_id, a.policy_id, a.policy_version) for a in atts]).encode()
            ).hexdigest()
            inputs_hash = hashlib.sha256(
                json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()[:16]
            key = (target_type, target_id, input_revision, sig, inputs_hash, amount_minor)
            if key in self._decisions:
                return self._decisions[key]
            verdict, results, reasons, parts = "allow", [], [], []
            for a in atts:
                try:
                    b = self.get(a.policy_id, a.policy_version)
                except NotFoundError:
                    reasons.append(f"unknown policy {a.policy_id} v{a.policy_version}")
                    results.append({"policy_id": a.policy_id, "clause": None, "outcome": "hold",
                                    "evidence": {"reason": "unknown_policy"}})
                    verdict = _worse(verdict, "hold")
                    continue
                parts.append((a.policy_id, a.policy_version, b.content_hash))
                if not b.verify():
                    reasons.append(f"snapshot hash mismatch for {a.policy_id} v{a.policy_version}")
                    results.append({"policy_id": a.policy_id, "clause": None, "outcome": "hold",
                                    "evidence": {"reason": "hash_mismatch"}})
                    verdict = _worse(verdict, "hold")
                    continue
                latest = self.get(a.policy_id).version
                if a.mandatory and a.policy_version != latest:
                    reasons.append(f"stale mandatory policy {a.policy_id}: attached v{a.policy_version}, latest v{latest}")
                    results.append({"policy_id": a.policy_id, "clause": None, "outcome": "hold",
                                    "evidence": {"reason": "stale_policy", "latest": latest}})
                    verdict = _worse(verdict, "hold")
                d: Decision = evaluate(b.as_policy(), inputs, input_revision, amount_minor)
                for r in d.results:
                    results.append({"policy_id": a.policy_id, "policy_version": a.policy_version, **r})
                verdict = _worse(verdict, d.verdict)
            if not atts:
                reasons.append("no effective policies attached")
                verdict = "hold"
            eff = hashlib.sha256(json.dumps(sorted(parts)).encode()).hexdigest()[:16]
            rec = EffectiveDecision(
                target_type, target_id, verdict, input_revision, eff,
                tuple((a.attachment_id, a.policy_id, a.policy_version, a.mandatory) for a in atts),
                tuple(results), tuple(reasons),
            )
            self._decisions[key] = rec
            return rec

    def decisions(self, target_type: str, target_id: str) -> list[EffectiveDecision]:
        with self._lock:
            return [d for k, d in self._decisions.items() if k[:2] == (target_type, target_id)]


def _worse(a: str, b: str) -> str:
    return a if RANK[a] >= RANK[b] else b


def default_registry(memory: PolicyMemory | None = None) -> PolicyRegistry:
    """Registry seeded with the org-base mandatory policy from treasury.policy."""
    from .policy import DEFAULT_POLICY
    reg = PolicyRegistry(memory)
    reg.publish(DEFAULT_POLICY.id, "Organisation base policy", DEFAULT_POLICY.clauses)
    reg.attach("organisation", "org", DEFAULT_POLICY.id, 1, 0, mandatory=True)
    return reg


