"""Customer delivery jobs: define, attach policies, preview (dry run), dispatch once, narrate.

A job is the customer-facing unit. Policies attach to the target ("job", job_id); the decision
engine already inherits that target through the mission's `job_id` input, so a dispatched job's
mission is decided under org rules + the job's attached policies, frozen at dispatch.

Authority stays in the backend: preview evaluates without writing anything and never pays.
Money is integer minor units; currency comes from the effective policy (CURRENCY-1) at call time.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from .decision import org_currency
from .ledger import Ledger
from .policies import ORG, PolicyError, PolicyRegistry
from .policy import Clause, evaluate

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, spec TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
  frozen TEXT, workflow TEXT, created_at TEXT NOT NULL);
"""

JOB_TARGET = "job"
INSURANCE_TEMPLATE = "tpl-insurance"


class JobError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class JobIn(BaseModel):
    pickup: str = Field(min_length=1, max_length=200)
    dropoff: str = Field(min_length=1, max_length=200)
    goods_value_minor: int = Field(ge=0, strict=True)
    price_minor: int = Field(gt=0, strict=True)
    delivery_fee_minor: int = Field(ge=0, strict=True)
    deadline: datetime
    insurance_required: bool = False
    currency: str | None = None
    client_ref: str | None = Field(default=None, max_length=80)

    @field_validator("pickup", "dropoff")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("currency")
    @classmethod
    def _cur(cls, v):
        if v is None:
            return v
        v = v.lower()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("currency must be a 3-letter ISO code")
        return v


# ---- policy templates (existing clause kinds only) --------------------------------------------

TEMPLATES: dict[str, dict] = {
    "tpl-fuel-meal": {
        "title": "Fuel and meal allowance",
        "summary": "Allowance payments above $30.00 need approval before they are released.",
        "clauses": [Clause("ALLOW-1", "Fuel and meal allowance above USD 30.00 needs approval",
                           "max_amount", {"limit_minor": 3000})],
    },
    INSURANCE_TEMPLATE: {
        "title": "Insurance required",
        "summary": "Payment waits until valid insurance cover is confirmed for the goods.",
        "clauses": [Clause("INSURE-1", "Release only while insurance cover is valid", "require_input",
                           {"key": "insurance_valid"})],
    },
    "tpl-approve-above": {
        "title": "Approve above a limit",
        "summary": "Any payment above the limit you choose needs a human approval.",
        "parameter": {"name": "limit_minor", "label": "Approval limit (minor units)", "default": 2500},
        "clauses": [Clause("LIMIT-1", "Payments above the chosen limit need approval", "max_amount",
                           {"limit_minor": 2500})],
    },
    "tpl-refund-before-pickup": {
        "title": "Refund before pickup",
        "summary": "Refunds are only allowed before pickup; a refund request after pickup is stopped.",
        "clauses": [Clause("REFUNDWIN-1", "Refund requested after pickup is not allowed", "forbid_input",
                           {"key": "refund_requested_after_pickup"})],
    },
}


def _bundle_summary(prose: list[str]) -> str:
    return "; ".join(prose)


def seed_templates(reg: PolicyRegistry) -> None:
    """Publish the optional templates once (idempotent)."""
    for pid, t in TEMPLATES.items():
        try:
            reg.get(pid)
        except PolicyError:
            reg.publish(pid, t["title"], t["clauses"])


def parametrised(reg: PolicyRegistry, policy_id: str, params: dict | None) -> tuple[str, int]:
    """Resolve (policy_id, default version). 'tpl-approve-above' with a limit publishes a derived policy."""
    seed_templates(reg)
    if policy_id == "tpl-approve-above" and params and "limit_minor" in params:
        lim = params["limit_minor"]
        if isinstance(lim, bool) or not isinstance(lim, int) or lim <= 0:
            raise JobError(422, "bad_param", "limit_minor must be a positive integer (minor units)")
        pid = f"tpl-approve-above-{lim}"
        try:
            reg.get(pid)
        except PolicyError:
            reg.publish(pid, f"Approve above {fmt_money(lim, org_cur(reg))}",
                        [Clause("LIMIT-1", f"Payments above {fmt_money(lim, org_cur(reg))} need approval",
                                "max_amount", {"limit_minor": lim})])
        return pid, reg.get(pid).version
    return policy_id, reg.get(policy_id).version


def org_cur(reg: PolicyRegistry) -> str:
    from .currency import DEFAULT_CURRENCY
    return org_currency(reg) or DEFAULT_CURRENCY


def allowed_currencies(reg: PolicyRegistry) -> list[str]:
    out: list[str] = []
    for a in reg.attachments_for(*ORG):
        for c in reg.get(a.policy_id, a.policy_version).clauses:
            if c.kind == "allowed_currency":
                out += [x for x in c.params["currencies"] if x not in out]
    return out


def fmt_money(minor: int, currency: str) -> str:
    major, cents = divmod(int(minor), 100)
    return f"${major:,}.{cents:02d}" if currency.lower() == "usd" else f"{major:,}.{cents:02d} {currency.upper()}"


def library(reg: PolicyRegistry) -> list[dict]:
    seed_templates(reg)
    mand = {a.policy_id for a in reg.attachments_for(*ORG) if a.mandatory}
    out = []
    for b in reg.list():
        t = TEMPLATES.get(b.policy_id)
        if b.policy_id.startswith("tpl-approve-above-"):
            continue  # derived from the parametrised template
        out.append({
            "policy_id": b.policy_id, "version": b.version, "title": b.title,
            "summary": t["summary"] if t else _bundle_summary([c.prose for c in b.clauses]),
            "template": t is not None, "mandatory": b.policy_id in mand,
            "locked": b.policy_id in mand, "parameter": t.get("parameter") if t else None,
            "clauses": [{"id": c.id, "prose": c.prose, "kind": c.kind, "params": c.params} for c in b.clauses],
        })
    return out


# ---- plain-English reasons --------------------------------------------------------------------

_INPUT_WORDS = {
    "receipt_confirmed": "delivery receipt has not been confirmed",
    "insurance_valid": "insurance cover has not been confirmed as valid",
    "prohibited_zone": "the route entered a prohibited zone",
    "refund_requested_after_pickup": "a refund was requested after pickup",
}


def plain_reason(outcome: str, evidence: dict, prose: str, currency: str) -> str:
    if outcome == "allow":
        return "Rule satisfied."
    if "missing" in evidence:
        k = evidence["missing"]
        return f"Held: {_INPUT_WORDS.get(k, 'required input ' + k + ' is missing')}. {prose}."
    if "present" in evidence:
        k = evidence["present"]
        return f"Stopped: {_INPUT_WORDS.get(k, k + ' is present')}. {prose}."
    if "limit_minor" in evidence:
        return (f"Held for approval: {fmt_money(evidence['amount_minor'], currency)} is above the "
                f"{fmt_money(evidence['limit_minor'], currency)} limit.")
    if "allowed" in evidence:
        return f"Stopped: currency {evidence.get('currency')} is not permitted (allowed: {', '.join(evidence['allowed'])})."
    return f"{outcome.capitalize()}: {prose}."


# ---- service ----------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(UTC).isoformat()


def default_start_workflow(mission_id: str) -> dict:
    """Start the durable mission workflow once; explicit 'unavailable' when it is not configured."""
    from dbos._error import DBOSException

    from . import workflows
    try:
        workflows.runtime()
        h = workflows.start_mission(mission_id)
    except (RuntimeError, DBOSException) as exc:  # not configured, or DBOS torn down
        return {"started": False, "unavailable": True, "message": str(exc)}
    return {"started": True, "workflow_id": h.get_workflow_id()}


class JobService:
    def __init__(self, ledger: Ledger, registry: Callable[[], PolicyRegistry],
                 start_workflow: Callable[[str], dict] = default_start_workflow,
                 mode: Callable[[], str] = lambda: "mock"):
        self.led = ledger
        self._reg = registry
        self.start_workflow = start_workflow
        self.mode = mode
        self._lock = threading.RLock()
        self.led.db.executescript(SCHEMA)
        self.led.db.commit()

    @property
    def reg(self) -> PolicyRegistry:
        return self._reg()

    # -- storage
    def _row(self, job_id: str):
        rows = self.led._read("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not rows:
            raise JobError(404, "not_found", f"unknown job {job_id}")
        return rows[0]

    def _view(self, r) -> dict:
        spec = json.loads(r["spec"])
        return {"job_id": r["id"], "mission_id": r["mission_id"], "status": r["status"], **spec,
                "total_minor": spec["price_minor"] + spec["delivery_fee_minor"],
                "frozen": json.loads(r["frozen"]) if r["frozen"] else None,
                "workflow": json.loads(r["workflow"]) if r["workflow"] else None,
                "created_at": r["created_at"], "simulated": True, "mode": self.mode()}

    def get(self, job_id: str) -> dict:
        return self._view(self._row(job_id))

    # -- create
    def create(self, body: JobIn) -> dict:
        reg = self.reg
        cur = (body.currency or org_cur(reg)).lower()
        allowed = allowed_currencies(reg)
        if allowed and cur not in allowed:
            raise JobError(422, "currency_not_permitted",
                           f"currency {cur} is not permitted by policy CURRENCY-1 (allowed: {', '.join(allowed)})")
        spec = {"pickup": body.pickup, "dropoff": body.dropoff, "goods_value_minor": body.goods_value_minor,
                "price_minor": body.price_minor, "delivery_fee_minor": body.delivery_fee_minor,
                "deadline": body.deadline.isoformat(), "insurance_required": body.insurance_required,
                "currency": cur}
        digest = hashlib.sha256(json.dumps([spec, body.client_ref], sort_keys=True).encode()).hexdigest()
        job_id, mission_id = f"job_{digest[:12]}", f"mission-job_{digest[:12]}"
        with self._lock:
            with self.led.transaction() as (c, audit):
                cur_ = c.execute("INSERT OR IGNORE INTO jobs(id,mission_id,spec,created_at) VALUES(?,?,?,?)",
                                 (job_id, mission_id, json.dumps(spec), _now()))
                if cur_.rowcount == 1:
                    audit.append("sender", "job.created",
                                 f"Job defined: {spec['pickup']} to {spec['dropoff']}, "
                                 f"{fmt_money(spec['price_minor'] + spec['delivery_fee_minor'], cur)} total",
                                 mission_id=mission_id, party="sender", payload={"job_id": job_id, **spec},
                                 mode=self.mode(), commit=False)
            if body.insurance_required and not any(
                    a.policy_id == INSURANCE_TEMPLATE for a in self.reg.attachments_for(JOB_TARGET, job_id)):
                self._attach(job_id, mission_id, INSURANCE_TEMPLATE, None, None, None, reason="insurance required by the job")
        return self.get(job_id)

    # -- policies
    def effective(self, job_id: str) -> dict:
        r = self._row(job_id)
        reg = self.reg
        rules = []
        for a in reg.effective_attachments([(JOB_TARGET, job_id)]):
            b = reg.get(a.policy_id, a.policy_version)
            rules.append({"attachment_id": a.attachment_id, "policy_id": a.policy_id, "version": a.policy_version,
                          "title": b.title, "mandatory": a.mandatory, "locked": a.mandatory or r["status"] != "draft",
                          "scope": "organisation" if a.target_type == "organisation" else "job",
                          "clauses": [{"id": c.id, "prose": c.prose, "kind": c.kind, "params": c.params}
                                      for c in b.clauses]})
        return {"job_id": job_id, "target_version": reg.target_version(JOB_TARGET, job_id),
                "frozen": r["status"] != "draft", "rules": rules}

    def _attach(self, job_id: str, mission_id: str, policy_id: str, version: int | None,
                expected_version: int | None, params: dict | None, reason: str = "") -> dict:
        reg = self.reg
        if policy_id in {a.policy_id for a in reg.attachments_for(*ORG) if a.mandatory}:
            raise JobError(400, "already_mandatory", f"{policy_id} is a mandatory organisation rule and already applies")
        policy_id, default_v = parametrised(reg, policy_id, params)
        version = version or default_v
        tv = reg.target_version(JOB_TARGET, job_id)
        att = reg.attach(JOB_TARGET, job_id, policy_id, version, tv if expected_version is None else expected_version)
        b = reg.get(policy_id, version)
        self.led.audit_note(mission_id, "sender", f"Policy attached: {b.title} (v{version}){' - ' + reason if reason else ''}",
                            {"job_id": job_id, "attachment": att.__dict__, "policy_hash": b.content_hash},
                            kind="job.policy_attached", party="sender", mode=self.mode())
        return att.__dict__

    def attach(self, job_id: str, policy_id: str, version: int | None, expected_version: int,
               params: dict | None = None) -> dict:
        r = self._row(job_id)
        with self._lock:
            if r["status"] != "draft":
                raise JobError(409, "job_frozen", "job is dispatched; its policies are frozen")
            self._attach(job_id, r["mission_id"], policy_id, version, expected_version, params)
        return self.effective(job_id)

    def detach(self, job_id: str, attachment_id: str, expected_version: int) -> dict:
        r = self._row(job_id)
        spec = json.loads(r["spec"])
        with self._lock:
            if r["status"] != "draft":
                raise JobError(409, "job_frozen", "job is dispatched; its policies are frozen")
            att = next((a for a in self.reg.attachments_for(JOB_TARGET, job_id) if a.attachment_id == attachment_id), None)
            if att is None:
                raise JobError(404, "not_found", "attachment not on this job")
            if att.policy_id == INSURANCE_TEMPLATE and spec["insurance_required"]:
                raise JobError(403, "locked", "insurance is required by this job and cannot be detached")
            self.reg.detach(attachment_id, expected_version)
            self.led.audit_note(r["mission_id"], "sender", f"Policy detached: {att.policy_id}", {"job_id": job_id},
                                kind="job.policy_detached", party="sender", mode=self.mode())
        return self.effective(job_id)

    # -- preview (no writes at all)
    def preview(self, job_id: str, situations: list[str] | None = None) -> dict:
        r = self._row(job_id)
        spec = json.loads(r["spec"])
        reg = self.reg
        cur = spec["currency"]
        total = spec["price_minor"] + spec["delivery_fee_minor"]
        atts = reg.effective_attachments([(JOB_TARGET, job_id)])
        bundles = [(a, reg.get(a.policy_id, a.policy_version)) for a in atts]
        limits = [c.params["limit_minor"] for _, b in bundles for c in b.clauses if c.kind == "max_amount"]
        base = {"currency": cur, "receipt_confirmed": True, "prohibited_zone": False,
                "insurance_valid": True, "refund_requested_after_pickup": False}
        over = max([total, *(x + 1 for x in limits)]) if limits else total
        defs = {
            "happy_path": ("Everything goes to plan", {}, total),
            "receipt_missing": ("Delivery receipt missing", {"receipt_confirmed": False}, total),
            "prohibited_zone": ("Courier enters a prohibited zone", {"prohibited_zone": True}, total),
            "over_limit": ("Amount goes over an approval limit", {}, over),
            "insurance_lapsed": ("Insurance cover has lapsed", {"insurance_valid": False}, total),
        }
        want = situations or list(defs)
        unknown = [s for s in want if s not in defs]
        if unknown:
            raise JobError(422, "unknown_situation", f"unknown situation(s): {', '.join(unknown)}")
        rank = {"allow": 0, "hold": 1, "deny": 2}
        out = []
        for name in want:
            label, patch, amount = defs[name]
            inputs = {**base, **patch}
            verdict, worst = "allow", None
            for a, b in bundles:
                d = evaluate(b.as_policy(), inputs, 0, amount)
                prose = {c.id: c.prose for c in b.clauses}
                for res in d.results:
                    if rank[res["outcome"]] > rank[verdict]:
                        verdict = res["outcome"]
                        worst = {"policy_id": a.policy_id, "clause": res["clause"], "outcome": res["outcome"],
                                 "reason": plain_reason(res["outcome"], res["evidence"], prose[res["clause"]], cur)}
            out.append({"situation": name, "label": label, "verdict": verdict, "amount_minor": amount,
                        "clause": worst["clause"] if worst else None,
                        "policy_id": worst["policy_id"] if worst else None,
                        "reason": worst["reason"] if worst else "All effective rules are satisfied; payment would be released.",
                        "simulated": True})
        return {"job_id": job_id, "side_effects": False, "simulated": True, "results": out}

    # -- dispatch (idempotent)
    def dispatch(self, job_id: str) -> dict:
        with self._lock:
            r = self._row(job_id)
            if r["status"] != "draft":
                return {**self.get(job_id), "already_dispatched": True}
            spec = json.loads(r["spec"])
            mission_id = r["mission_id"]
            reg = self.reg
            if spec["insurance_required"] and not any(
                    a.policy_id == INSURANCE_TEMPLATE for a in reg.attachments_for(JOB_TARGET, job_id)):
                self._attach(job_id, mission_id, INSURANCE_TEMPLATE, None, None, None, reason="insurance required by the job")
            frozen = []
            for a in reg.effective_attachments([(JOB_TARGET, job_id)]):
                b = reg.get(a.policy_id, a.policy_version)
                frozen.append({"policy_id": a.policy_id, "version": a.policy_version,
                               "content_hash": b.content_hash, "mandatory": a.mandatory})
            total = spec["price_minor"] + spec["delivery_fee_minor"]
            self.led.create_mission(mission_id, total, spec["currency"])
            self.led.apply_event(f"{job_id}:dispatch", mission_id, 0,
                                 {"job_id": job_id, "insurance_required": spec["insurance_required"]},
                                 actor="sender", party="sender", kind="job.inputs",
                                 summary="Job details recorded as mission inputs")
            reg.lock_target(JOB_TARGET, job_id)
            wf = self.start_workflow(mission_id)
            with self.led.transaction() as (c, audit):
                c.execute("UPDATE jobs SET status='dispatched', frozen=?, workflow=? WHERE id=?",
                          (json.dumps(frozen), json.dumps(wf), job_id))
                audit.append("sender", "job.dispatched",
                             f"Job dispatched with {len(frozen)} policies frozen", mission_id=mission_id,
                             party="sender", payload={"job_id": job_id, "frozen": frozen, "total_minor": total},
                             mode=self.mode(), commit=False)
                if wf.get("started"):
                    audit.append("system", "job.workflow_started", "Durable payment workflow started",
                                 mission_id=mission_id, party="sender",
                                 payload={"job_id": job_id, **wf}, mode=self.mode(), commit=False)
                else:
                    audit.append("system", "job.workflow_unavailable",
                                 "Payment workflow is unavailable; the job is recorded but nothing will be paid",
                                 mission_id=mission_id, party="sender", payload={"job_id": job_id, **wf},
                                 mode=self.mode(), commit=False)
            return {**self.get(job_id), "already_dispatched": False}

    # -- watch
    def watch(self, job_id: str, after: int = 0) -> dict:
        r = self._row(job_id)
        entries = self.led.audit.query(mission_id=r["mission_id"], after=after, limit=500)
        steps = [narrate(e) for e in entries]
        everything = entries if after == 0 else self.led.audit.query(mission_id=r["mission_id"], limit=1000)
        return {"job_id": job_id, "mission_id": r["mission_id"], "job_status": r["status"],
                "status": progress(r["status"], everything), "mode": self.mode(), "steps": steps,
                "last_seq": entries[-1]["seq"] if entries else after,
                "done": progress(r["status"], everything) in ("paid", "denied")}


def progress(job_status: str, entries: list[dict]) -> str:
    if job_status == "draft":
        return "draft"
    state = "dispatched"
    for e in entries:
        if e["kind"] == "payment" and (e.get("payload") or {}).get("state") == "confirmed":
            return "paid"
        if e["kind"] == "decision":
            v = (e.get("policy") or {}).get("verdict")
            state = {"deny": "denied", "hold": "held", "allow": "approved"}.get(v, state)
    return state


def narrate(e: dict) -> dict:
    kind, p, mode = e["kind"], e.get("payload") or {}, e.get("mode")
    cur = p.get("currency", "usd")
    simulated = mode != "stripe_test"
    if kind == "decision":
        pol = e.get("policy") or {}
        v = pol.get("verdict", "hold")
        bad = [c for c in (pol.get("clauses") or []) if isinstance(c, dict) and c.get("outcome") != "allow"]
        why = "; ".join(plain_reason(c["outcome"], c.get("evidence") or {}, c.get("clause", ""), cur) for c in bad[:3])
        text = {"allow": "Policy check passed: payment may be released.",
                "hold": "Policy check: payment is on hold. " + why,
                "deny": "Policy check: payment is stopped. " + why}.get(v, e["summary"])
    elif kind == "payment":
        text = (f"Payment {p.get('state', 'recorded')} "
                f"({'Stripe test mode' if mode == 'stripe_test' else 'simulated, no real money'}).")
    elif kind == "input.event":
        patch = p.get("patch") or {}
        text = "New information received: " + (", ".join(_INPUT_WORDS_OK.get(k, k) for k in patch) or "update") + "."
    else:
        text = {"job.created": e["summary"], "job.policy_attached": e["summary"], "job.policy_detached": e["summary"],
                "job.dispatched": e["summary"] + ".", "job.workflow_started": "Payment workflow started; it now waits for evidence.",
                "job.workflow_unavailable": e["summary"] + ".", "job.inputs": "Job details recorded.",
                "mission.created": "Payment mission opened for this job."}.get(kind, e["summary"])
    return {"seq": e["seq"], "ts": e["ts"], "kind": kind, "actor": e["actor"], "party": e.get("party"),
            "text": text, "mode": mode or "mock", "simulated": simulated}


_INPUT_WORDS_OK = {"receipt_confirmed": "delivery receipt", "insurance_valid": "insurance status",
                   "prohibited_zone": "route zone", "job_id": "job details"}
