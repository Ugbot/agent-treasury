"""AI-assisted policy authoring. The model only *drafts*: it may emit the four supported clause kinds
with typed params, and everything it cannot express is reported as `unsupported`. Drafts are validated
with the same checks as publish. This module never publishes or attaches anything."""
from __future__ import annotations

import json
import os
import re

from .audit import redact
from .policies import PolicyError, PolicyRegistry, _check_clauses, _weakens
from .policy import Clause

MODEL = "claude-sonnet-5-5"
MAX_CLAUSES, MAX_PROSE, MAX_TITLE, MAX_CURRENCIES = 50, 500, 100, 10
SETUP = "Set ANTHROPIC_API_KEY on the server to enable AI drafting. You can still write rules by hand."
KINDS = ("max_amount", "require_input", "forbid_input", "allowed_currency",
         "allowed_counterparties", "velocity_limit", "payment_freeze")
CLAUSE_ID = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+")
POLICY_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
# Inputs the server produces; require/forbid are only meaningful on boolean facts.
KNOWN_INPUTS = [
    {"key": "receipt_confirmed", "label": "Customer confirmed receipt", "type": "boolean"},
    {"key": "prohibited_zone", "label": "Route enters a prohibited zone", "type": "boolean"},
    {"key": "insured", "label": "Driver is insured", "type": "boolean"},
    {"key": "location", "label": "Named location (text; cannot be required or forbidden)", "type": "text",
     "usable": False},
]
CLAUSE_KINDS = [
    {"kind": "max_amount", "title": "Approval limit", "params": {"limit_minor": "integer, minor units, >= 0"},
     "template": "Hold any payment over {amount} until a person approves it", "verdict_if_unmet": "hold"},
    {"kind": "require_input", "title": "Require a fact", "params": {"key": "string, a known input key"},
     "template": "Hold until {key} is confirmed", "verdict_if_unmet": "hold"},
    {"kind": "forbid_input", "title": "Forbid a fact", "params": {"key": "string, a known input key"},
     "template": "Deny if {key} is present", "verdict_if_unmet": "deny"},
    {"kind": "allowed_currency", "title": "Allowed currencies",
     "params": {"currencies": "list of lowercase 3-letter ISO codes"},
     "template": "Only settle in {currencies}", "verdict_if_unmet": "deny"},
    {"kind": "allowed_counterparties", "title": "Approved payees",
     "params": {"recipients": "list of payee ids (may be empty if categories is set)",
                "categories": "list of merchant categories (may be empty if recipients is set)"},
     "template": "Only pay these payees: {recipients}", "verdict_if_unmet": "deny"},
    {"kind": "velocity_limit", "title": "Payment frequency limit",
     "params": {"max_payments": "integer >= 1", "window_seconds": "integer >= 1"},
     "template": "No more than {max_payments} payments every {minutes} minutes", "verdict_if_unmet": "hold"},
    {"kind": "payment_freeze", "title": "Payment freeze", "params": {"active": "boolean"},
     "template": "Payments are frozen", "verdict_if_unmet": "hold"},
]

SYSTEM = f"""You draft payment policies for a treasury. You have NO authority: a human reviews and publishes.
Call the propose_policy tool exactly once.
Rules:
- Only these clause kinds exist: max_amount {{limit_minor:int}}, require_input {{key:str}}, forbid_input {{key:str}}, allowed_currency {{currencies:[str]}}, allowed_counterparties {{recipients:[str],categories:[str]}}, velocity_limit {{max_payments:int,window_seconds:int}}, payment_freeze {{active:bool}}.
- Known input keys: {", ".join(k["key"] for k in KNOWN_INPUTS if k.get("usable", True))}. Use only these keys.
- Amounts are INTEGER MINOR UNITS in the policy currency (e.g. cents). 'over $50' -> max_amount limit_minor 5000; 'above 120 euros' -> 12000.
- Currencies are lowercase ISO codes ('usd', 'eur').
- Clause ids are stable and unique like 'MEAL-1', 'LIMIT-1' (UPPERCASE-NUMBER). When editing a base policy keep existing ids for unchanged rules.
- prose is one short plain-English sentence per clause.
- If the request contains anything these kinds cannot express (per-person limits, anything else), put it in `unsupported` with a reason. NEVER approximate it with a different clause and NEVER drop it silently.
- policy_id: lowercase slug. title: short."""

TOOL = {
    "name": "propose_policy",
    "description": "Propose a draft policy using only supported clause kinds; list anything inexpressible as unsupported.",
    "input_schema": {
        "type": "object",
        "properties": {
            "policy_id": {"type": "string", "pattern": "^" + POLICY_ID.pattern + "$"},
            "title": {"type": "string"},
            "clauses": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string", "pattern": "^" + CLAUSE_ID.pattern + "$"},
                "prose": {"type": "string"},
                "kind": {"type": "string", "enum": list(KINDS)},
                "params": {"type": "object"}},
                "required": ["id", "prose", "kind", "params"]}},
            "unsupported": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "reason": {"type": "string"}}, "required": ["text", "reason"]}},
        },
        "required": ["policy_id", "title", "clauses", "unsupported"],
    },
}


def client_from_env():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=key, timeout=45, max_retries=1)


_client_factory = client_from_env


def set_client_factory(f) -> None:
    global _client_factory
    _client_factory = f or client_from_env


# --- validation (same checks as publish) ---------------------------------------------------------
def validate_clauses(clauses: list[dict]) -> list[dict]:
    """Return [{clause, message}] for every problem; empty means publishable."""
    errors: list[dict] = []
    if not isinstance(clauses, list) or not clauses:
        return [{"clause": None, "message": "policy needs at least one clause"}]
    if len(clauses) > MAX_CLAUSES:
        return [{"clause": None, "message": f"at most {MAX_CLAUSES} clauses allowed"}]
    usable = {k["key"] for k in KNOWN_INPUTS if k.get("usable", True)}
    seen: set[str] = set()
    for i, c in enumerate(clauses):
        cid = c.get("id") if isinstance(c, dict) else None
        if not isinstance(c, dict) or not isinstance(cid, str) or not cid.strip():
            errors.append({"clause": None, "message": f"clause #{i + 1}: id is required"})
            continue
        if cid in seen:
            errors.append({"clause": cid, "message": f"duplicate clause id {cid}"})
            continue
        seen.add(cid)
        if not CLAUSE_ID.fullmatch(cid):
            errors.append({"clause": cid, "message": f"{cid}: id must look like 'MEAL-1'"})
        if not isinstance(c.get("prose"), str) or not c["prose"].strip():
            errors.append({"clause": cid, "message": f"{cid}: prose is required"})
        elif len(c["prose"]) > MAX_PROSE:
            errors.append({"clause": cid, "message": f"{cid}: prose must be at most {MAX_PROSE} characters"})
        if c.get("kind") not in KINDS:
            errors.append({"clause": cid, "message": f"{cid}: unknown kind {c.get('kind')!r}"})
            continue
        if not isinstance(c.get("params"), dict):
            errors.append({"clause": cid, "message": f"{cid}: params must be an object"})
            continue
        p = c["params"]
        if c["kind"] == "max_amount":
            lim = p.get("limit_minor")
            if isinstance(lim, int) and not isinstance(lim, bool) and not 1 <= lim <= 10**9:
                errors.append({"clause": cid, "message": f"{cid}: limit_minor must be between 1 and 10**9"})
                continue
        elif c["kind"] in ("require_input", "forbid_input"):
            if isinstance(p.get("key"), str) and p["key"] not in usable:
                errors.append({"clause": cid, "message": f"{cid}: unknown input key {p['key']!r}"})
                continue
        elif c["kind"] == "allowed_currency":
            if isinstance(p.get("currencies"), list) and len(p["currencies"]) > MAX_CURRENCIES:
                errors.append({"clause": cid, "message": f"{cid}: at most {MAX_CURRENCIES} currencies"})
                continue
        try:
            _check_clauses([Clause(cid, str(c.get("prose", "")), c["kind"], c["params"])])
        except PolicyError as e:
            errors.append({"clause": cid, "message": str(e)})
    return errors


def to_clauses(clauses: list[dict]) -> tuple[Clause, ...]:
    return tuple(Clause(c["id"], c["prose"], c["kind"], c["params"]) for c in clauses)


def _money(minor: int, currency: str) -> str:
    v = f"{minor / 100:.2f}"
    return f"${v}" if currency.lower() == "usd" else f"{currency.upper()} {v}"


def _human(key: str) -> str:
    return key.replace("_", " ").strip() or "the required input"


_NEED = {"receipt_confirmed": "Release only after the customer confirms receipt",
         "prohibited_zone": "Hold until the route is outside every prohibited zone",
         "insured": "Hold until the driver is insured"}
_FORBID = {"receipt_confirmed": "Deny if the customer has confirmed receipt",
           "prohibited_zone": "Deny if the route enters a prohibited zone",
           "insured": "Deny if the driver is insured"}


def sentence(c: dict, currency: str = "usd") -> tuple[str, str]:
    """(plain-English text, verdict if unmet); mirrors frontend/src/policies/clauses.ts."""
    p, kind = c.get("params") or {}, c.get("kind")
    if kind == "max_amount":
        return f"Hold any payment over {_money(int(p['limit_minor']), currency)} until a person approves it", "hold"
    if kind == "require_input":
        return _NEED.get(p["key"], f"Hold until {_human(p['key'])} is confirmed"), "hold"
    if kind == "forbid_input":
        return _FORBID.get(p["key"], f"Deny if {_human(p['key'])} is present"), "deny"
    if kind == "allowed_counterparties":
        names = [*p.get("recipients", []), *p.get("categories", [])]
        return "Only pay these payees: " + ", ".join(names), "deny"
    if kind == "velocity_limit":
        m = p["window_seconds"] / 60
        m = int(m) if m == int(m) else round(m, 1)
        return f"No more than {p['max_payments']} payments every {m} minutes", "hold"
    if kind == "payment_freeze":
        return ("Payments are frozen" if p.get("active") else "Payments are not frozen"), "hold"
    if kind == "allowed_currency":
        cs = [x.upper() for x in p["currencies"]]
        joined = cs[0] if len(cs) == 1 else f"{', '.join(cs[:-1])} or {cs[-1]}"
        return f"Only settle in {joined}", "deny"
    return str(c.get("prose", "")), "hold"


def weakenings(registry: PolicyRegistry, policy_id: str, clauses: list[dict]) -> list[str]:
    """Every way the new clauses weaken the current version (empty if none or new policy)."""
    try:
        cur = registry.get(policy_id)
    except PolicyError:
        return []
    new = to_clauses(clauses)
    out = []
    for o in cur.clauses:
        try:
            w = _weakens((o,), new)
        except (KeyError, TypeError):
            w = f"clause {o.id} has invalid parameters"
        if w:
            out.append(w)
    return out


def is_mandatory(registry: PolicyRegistry, policy_id: str) -> bool:
    return any(a.policy_id == policy_id and a.mandatory
               for a in registry.attachments_for("organisation", "org"))


def diff(registry: PolicyRegistry, policy_id: str, clauses: list[dict]) -> dict:
    try:
        old = {c.id: {"id": c.id, "prose": c.prose, "kind": c.kind, "params": c.params}
               for c in registry.get(policy_id).clauses}
    except PolicyError:
        old = {}
    new = {c["id"]: {"id": c["id"], "prose": c["prose"], "kind": c["kind"], "params": c["params"]}
           for c in clauses if isinstance(c, dict) and isinstance(c.get("id"), str)}
    text = lambda c: sentence(c)[0]
    return {"added": [text(v) for k, v in new.items() if k not in old],
            "removed": [text(v) for k, v in old.items() if k not in new],
            "changed": [{"id": k, "before": text(old[k]), "after": text(v)} for k, v in new.items()
                        if k in old and old[k] != v]}


def current_version(registry: PolicyRegistry, policy_id: str) -> int:
    try:
        return registry.get(policy_id).version
    except PolicyError:
        return 0


# --- dry run ---------------------------------------------------------------------------------------
SITUATIONS = {
    "all_in_order": ("All in order", 4200, {}),
    "receipt_missing": ("Receipt missing", 4200, {"receipt_confirmed": False}),
    "prohibited_zone": ("Prohibited zone", 4200, {"prohibited_zone": True}),
    "over_limit": ("Over limit", 9000, {}),
    "wrong_currency": ("Wrong currency", 4200, {"currency": "__other__"}),
    "payee_not_allowed": ("Payee not on the list", 4200, {"recipient": "__unlisted_payee__",
                                                          "merchant_category": "__unlisted_category__"}),
    "frozen": ("Payments frozen", 4200, {}),
}


def _reason(r: dict, currency: str = "usd") -> str:
    ev = r.get("evidence") or {}
    if "amount_minor" in ev and "limit_minor" in ev:
        return (f"amount {_money(int(ev['amount_minor']), currency)} is over the limit "
                f"{_money(int(ev['limit_minor']), currency)}")
    if "currency" in ev and "allowed" in ev:
        return f"{str(ev['currency']).upper()} is not an allowed currency"
    if ev.get("frozen"):
        return "payments are frozen"
    if "payments_in_window" in ev:
        return f"{ev['payments_in_window']} payments already in the last {int(ev['window_seconds'] // 60)} minutes"
    if "recipient" in ev or "merchant_category" in ev:
        return f"{ev.get('recipient') or ev.get('merchant_category')} is not an approved payee"
    if ev.get("missing") == "currency":
        return "no currency was supplied"
    if "missing" in ev:
        return f"still waiting on: {_human(ev['missing'])}"
    if "present" in ev:
        return f"found: {_human(ev['present'])}"
    return "all rules met" if r.get("outcome") == "allow" else "rule not met"


def dry_run(real: PolicyRegistry, clauses: list[dict], situations: list[str] | None = None,
            policy_id: str = "draft") -> list[dict]:
    """Evaluate the draft on top of the mandatory org policies in a throwaway registry via
    PolicyRegistry.decide. Nothing is persisted, attached to a real target, or paid."""
    from .decision import org_currency
    from .policies import PolicyRegistry as Reg
    sandbox = Reg()  # no memory backend, no audit instrumentation: no side effects
    replaced = False
    done: set[str] = set()
    for a in real.attachments_for("organisation", "org"):
        if not a.mandatory or a.policy_id in done:
            continue
        done.add(a.policy_id)
        if a.policy_id == policy_id:
            replaced = True  # draft stands in for this mandatory policy
            continue
        for v in range(1, a.policy_version + 1):
            b = real.get(a.policy_id, v)
            sandbox.publish(a.policy_id, b.title, b.clauses, v - 1)
        sandbox.attach("organisation", "org", a.policy_id, a.policy_version,
                       sandbox.target_version("organisation", "org"), mandatory=True)
    if replaced:
        real_b = real.get(policy_id)
        for v in range(1, real_b.version):
            b = real.get(policy_id, v)
            sandbox.publish(policy_id, b.title, b.clauses, v - 1)
        sb = sandbox.publish(policy_id, real_b.title, to_clauses(clauses), real_b.version - 1)
        sandbox.attach("organisation", "org", policy_id, sb.version,
                       sandbox.target_version("organisation", "org"), mandatory=True)
    else:
        sb = sandbox.publish(policy_id, "Draft", to_clauses(clauses), 0)
        sandbox.attach("payment", "dry-run", policy_id, sb.version, 0)
    cur = org_currency(real) or "usd"
    other = "eur" if cur != "eur" else "usd"
    out = []
    for sid in situations or list(SITUATIONS):
        if sid not in SITUATIONS:
            raise ValueError(f"unknown situation {sid!r}")
        label, amount, over = SITUATIONS[sid]
        inputs = {"receipt_confirmed": True, "prohibited_zone": False, "currency": cur, "insured": True}
        inputs.update({k: (other if v == "__other__" else v) for k, v in over.items()})
        # server facts a draft is judged against: a listed payee (first allowed) unless the situation overrides
        inputs.setdefault("now", 1_000_000)
        inputs.setdefault("payment_times", [])
        for c in to_clauses(clauses):
            if c.kind == "allowed_counterparties":
                if c.params.get("recipients"):
                    inputs.setdefault("recipient", c.params["recipients"][0])
                if c.params.get("categories"):
                    inputs.setdefault("merchant_category", c.params["categories"][0])
        d = sandbox.decide("payment", "dry-run", inputs, 1, amount)
        bad = [r for r in d.results if r.get("outcome") != "allow"]
        worst = max(bad, key=lambda r: {"hold": 1, "deny": 2}[r["outcome"]]) if bad else None
        out.append({"situation": sid, "label": label, "verdict": d.verdict,
                    "clause": worst.get("clause") if worst else None,
                    "reason": _reason(worst, cur) if worst else (d.reasons[0] if d.reasons else "all rules met")})
    return out


# --- drafting --------------------------------------------------------------------------------------
class DraftError(Exception):
    def __init__(self, code: str, message: str, errors: list[dict] | None = None, status: int = 502):
        super().__init__(message)
        self.code, self.errors, self.status = code, errors or [], status


def draft(registry: PolicyRegistry, description: str, base_policy_id: str | None = None) -> dict:
    """Ask the model for a draft. Returns the contract body for POST /author/draft."""
    base = {"available": False, "setup": SETUP, "unsupported": [], "ai_drafted": True}
    client = _client_factory()
    if client is None:
        return base
    base_bundle = None
    if base_policy_id:
        try:
            base_bundle = registry.get(base_policy_id)
        except PolicyError as e:
            raise DraftError("not_found", f"base policy {base_policy_id!r} not found", status=404) from e
    user = "Request (plain English):\n" + redact(description)
    if base_bundle:
        user += ("\n\nBase policy to edit (keep ids for unchanged rules, policy_id must stay "
                 f"{base_bundle.policy_id!r}):\n" + json.dumps(base_bundle.to_dict()["clauses"]))
    try:
        resp = client.messages.create(model=MODEL, max_tokens=1500, system=SYSTEM, tools=[TOOL],
                                      tool_choice={"type": "tool", "name": "propose_policy"},
                                      messages=[{"role": "user", "content": user}])
    except Exception as e:
        raise DraftError("model_error", f"drafting model call failed: {type(e).__name__}") from e
    raw = next((b.input for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
    if raw is None:
        raise DraftError("invalid_model_output", "model returned no policy proposal")
    if not isinstance(raw, dict):
        raise DraftError("invalid_model_output", "model returned a malformed policy proposal")
    out = dict(raw)
    if out.get("clauses") == [] and isinstance(out.get("unsupported"), list) and out["unsupported"]:
        # nothing expressible: report it all as unsupported, no draft
        if not all(_is_unsupported(u) for u in out["unsupported"]):
            raise DraftError("invalid_model_output", "unsupported entries must be {text, reason}")
        return {**base, "available": True, "setup": None, "model": MODEL,
                "unsupported": [{"text": u["text"], "reason": u["reason"]} for u in out["unsupported"]]}
    errors = validate_model_output(out, base_bundle.policy_id if base_bundle else None)
    if errors:
        raise DraftError("invalid_model_output", "model output failed validation; no draft produced", errors)
    clauses = [{"id": c["id"], "prose": c["prose"], "kind": c["kind"], "params": c["params"]}
               for c in out["clauses"]]
    unsupported = [{"text": u["text"], "reason": u["reason"]} for u in out["unsupported"]]
    return {"available": True, "model": MODEL, "ai_drafted": True, "unsupported": unsupported,
            "draft": {"policy_id": out["policy_id"], "title": out["title"].strip(), "clauses": clauses}}


def _is_unsupported(u) -> bool:
    return isinstance(u, dict) and isinstance(u.get("text"), str) and isinstance(u.get("reason"), str)


def validate_model_output(out: dict, required_policy_id: str | None) -> list[dict]:
    errors: list[dict] = []
    pid = out.get("policy_id")
    if not isinstance(pid, str) or not POLICY_ID.fullmatch(pid):
        errors.append({"clause": None, "message": "policy_id must be a lowercase slug"})
    elif required_policy_id and pid != required_policy_id:
        errors.append({"clause": None, "message": f"policy_id must stay {required_policy_id}"})
    if not isinstance(out.get("title"), str) or not out["title"].strip():
        errors.append({"clause": None, "message": "title is required"})
    elif len(out["title"]) > MAX_TITLE:
        errors.append({"clause": None, "message": f"title must be at most {MAX_TITLE} characters"})
    uns = out.get("unsupported")
    if not isinstance(uns, list) or not all(isinstance(u, dict) and isinstance(u.get("text"), str)
                                            and isinstance(u.get("reason"), str) for u in uns):
        errors.append({"clause": None, "message": "unsupported must be a list of {text, reason}"})
    cl = out.get("clauses")
    if not isinstance(cl, list):
        return errors + [{"clause": None, "message": "clauses must be a list"}]
    errors += validate_clauses(cl)
    return errors
