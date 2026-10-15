"""Live CURRENCY-1 evaluation shared by refunds, proof settlement and fuel.

Always resolved at call time from the effective policy: the registry's organisation-level
attachments when a registry is given, otherwise ``treasury.policy.DEFAULT_POLICY`` read live
(never an import-time copy). Fails closed: a missing currency or no allowed_currency clause
anywhere falls back to the default policy clause.
"""
from __future__ import annotations

from . import policy as _policy
from .policy import Clause, Policy, evaluate


def _currency_clauses(registry) -> list[tuple[str, int, Clause]]:
    out: list[tuple[str, int, Clause]] = []
    if registry is not None:
        try:
            for a in registry.effective_attachments([]):
                b = registry.get(a.policy_id, a.policy_version)
                out += [(b.policy_id, b.version, c) for c in b.clauses if c.kind == "allowed_currency"]
        except Exception:  # noqa: BLE001 - unreadable registry: fall through to default policy
            out = []
    if not out:
        p = _policy.DEFAULT_POLICY
        out = [(p.id, p.version, c) for c in p.clauses if c.kind == "allowed_currency"]
    return out


def live_default_currency(registry=None) -> str:
    cl = _currency_clauses(registry)
    return cl[0][2].params["currencies"][0] if cl else "usd"


def currency_denial(currency: str | None, registry=None) -> dict | None:
    """None when permitted, else a dict naming the CURRENCY-1 clause and the policy."""
    for pid, ver, c in _currency_clauses(registry):
        d = evaluate(Policy(pid, ver, (c,)), {"currency": currency}, 0, 0)
        if d.verdict != "allow":
            return {"clause": c.id, "policy_id": pid, "policy_version": ver, "verdict": "deny",
                    "currency": str(currency or "").lower(), "allowed": list(c.params["currencies"]),
                    "reason": f"denied by {c.id}: currency '{str(currency or '').lower()}' not in "
                              f"{list(c.params['currencies'])}"}
    return None
