"""Truthful decision core: one place that turns ledger state into an effective policy decision.

- Evaluates org-base + every attached/chained policy (PolicyRegistry) for the mission, its
  payment, job, card and counterparty. DEFAULT_POLICY is only the seed of the registry.
- Safety facts (receipt, position, zone) are produced server-side from proof.py (nonce flow)
  and geo.py; the client may only send plain attestations of an allow-listed shape.
- A deny is sticky: it survives a later input unless the effective policy changes or an
  approver records an audited override.
- APPROVE-1 style holds (max_amount) are resolved only by an approval bound to amount,
  currency, recipient and the effective policy hash, from an approver role.
- Every input, decision (all clause results and evidence), approval and override is audited.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from .geo import Point, point_in_polygon
from .ledger import Ledger, approval_digest
from .policies import RANK, NotFoundError, PolicyRegistry
from .proof import Order, ProofError, ReceiptNonces, verify_proof

APPROVER_ROLES = frozenset({"approver"})

# Facts only the server may write. Clients sending these are rejected (and audited).
SERVER_OWNED = frozenset({
    "receipt_confirmed", "receipt_proof", "location", "prohibited_zone", "zone", "zones_entered",
    "currency", "amount_minor", "attached_policy", "last_approval", "last_override",
    "recipient", "merchant_category", "payment_times", "now",
})
# Client attestations with a strict type.
BOOL_KEYS = frozenset({"insured"})
STR_KEYS = frozenset({"scenario", "job_id", "card_id", "counterparty_id"})
SCENARIOS = frozenset({"happy-path", "prohibited-zone", "over-limit"})

# Simulated world (the demo map): named places the sim driver can be at.
DESTINATION = Point(51.5000, -0.1200)
LOCATIONS: dict[str, Point] = {
    "depot": Point(51.5000, -0.2000),
    "route": Point(51.5000, -0.1600),
    "destination": Point(51.5000, -0.1200),
    "prohibited": Point(51.5200, -0.1000),
}
LOCATION_ALIASES = {"dropoff": "destination", "customer": "destination", "restricted": "prohibited"}
PROHIBITED_ZONES: dict[str, tuple[Point, ...]] = {
    "zone-restricted-1": (Point(51.515, -0.105), Point(51.525, -0.105),
                          Point(51.525, -0.095), Point(51.515, -0.095)),
}


class InputError(Exception):
    """Client input rejected (HTTP 422 / 403)."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


class ProofRejected(Exception):
    def __init__(self, gates: dict, reasons: dict):
        super().__init__("receipt proof rejected: " + "; ".join(reasons.values()))
        self.gates, self.reasons = gates, reasons


class ApprovalError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Actor:
    id: str = "anonymous"
    role: str = "client"


ANON = Actor()
SIM_SENDER = Actor("sim", "sender")


def resolve_location(name: str) -> tuple[str, Point]:
    key = LOCATION_ALIASES.get(str(name).lower(), str(name).lower())
    if key not in LOCATIONS:
        raise InputError("unknown_location", f"unknown location {name!r}; known: {sorted(LOCATIONS)}")
    return key, LOCATIONS[key]


def zones_at(p: Point) -> list[str]:
    return [zid for zid, poly in PROHIBITED_ZONES.items() if point_in_polygon(p, poly)]


def validate_client_inputs(inputs: dict) -> dict:
    """Strict, fail-closed validation of client-supplied inputs. Returns the clean patch."""
    if not isinstance(inputs, dict):
        raise InputError("bad_inputs", "inputs must be an object")
    bad = sorted(k for k in inputs if k in SERVER_OWNED)
    if bad:
        raise InputError("server_owned_fact",
                         f"{bad} are server-owned facts (proof/geo) and cannot be sent by a client", 403)
    clean = {}
    for k, v in inputs.items():
        if not isinstance(k, str) or not k:
            raise InputError("bad_inputs", "input keys must be non-empty strings")
        if k in BOOL_KEYS and not isinstance(v, bool):
            raise InputError("bad_type", f"{k} must be a boolean, got {type(v).__name__}")
        if k in STR_KEYS and not isinstance(v, str):
            raise InputError("bad_type", f"{k} must be a string, got {type(v).__name__}")
        if k == "scenario" and v not in SCENARIOS:
            raise InputError("bad_scenario", "unknown scenario")
        if not isinstance(v, (bool, int, float, str)):
            raise InputError("bad_type", f"{k} must be a scalar")
        clean[k] = v
    return clean


def server_facts(audit, mission_id: str | None, recipient: str | None, category: str | None = None,
                 now: float | None = None) -> dict:
    """Server-owned inputs for allowed_counterparties / velocity_limit. Never taken from a client.
    payment_times are epoch seconds of confirmed payments (audit kind 'payment') other than this mission's."""
    import time
    from datetime import datetime
    out: dict = {"now": int(now if now is not None else time.time())}
    if recipient:
        out["recipient"] = recipient
    if category:
        out["merchant_category"] = category
    times: list[float] = []
    if audit is not None:
        for e in audit.query(kind="payment", limit=100000):
            if e.get("mission_id") != mission_id:
                try:
                    times.append(datetime.fromisoformat(e["ts"]).timestamp())
                except (ValueError, TypeError):
                    continue
    out["payment_times"] = times
    return out


def clause_reasons(registry: PolicyRegistry, inputs: dict) -> list[str]:
    """Reason codes from the allowed_counterparties / payment_freeze clauses of the mandatory org policies,
    for paths (fuel card authorisation) that decide locally. Empty list means none objects."""
    from .policy import evaluate
    out: list[str] = []
    for a in registry.attachments_for("organisation", "org"):
        b = registry.get(a.policy_id, a.policy_version)
        kinds = [c for c in b.clauses if c.kind in ("allowed_counterparties", "payment_freeze", "velocity_limit")]
        if not kinds:
            continue
        pol = b.as_policy()
        from .policy import Policy
        for c in kinds:
            res = evaluate(Policy(pol.id, pol.version, (c,)), inputs, 0, 0).results[0]
            if res["outcome"] == "allow":
                continue
            code = {"allowed_counterparties": "merchant_category_not_permitted",
                    "payment_freeze": "payments_frozen", "velocity_limit": "velocity_limit"}[c.kind]
            out.append(f"{code}:{c.id}")
    return out


def org_currency(registry: PolicyRegistry) -> str | None:
    """Default settlement currency implied by the mandatory organisation policies (CURRENCY-1)."""
    from .policy import default_currency
    for a in registry.attachments_for("organisation", "org"):
        cur = default_currency(registry.get(a.policy_id, a.policy_version).as_policy())
        if cur:
            return cur
    return None


class DecisionEngine:
    def __init__(self, ledger: Ledger, registry: Callable[[], PolicyRegistry] | None = None,
                 mode: Callable[[], str] | None = None, clock: Callable[[], float] = time.time):
        self.led = ledger
        self._registry = registry
        self._mode = mode or (lambda: "mock")
        self.clock = clock
        self.nonces = ReceiptNonces()
        self._orders: dict[str, Order] = {}

    @property
    def registry(self) -> PolicyRegistry:
        if self._registry is not None:
            return self._registry()
        from .routes_policies import get_registry
        return get_registry()

    @property
    def mode(self) -> str:
        return self._mode()

    # ------------------------------------------------------------ inputs
    def submit_inputs(self, mission_id: str, event_id: str, expected_revision: int, inputs: dict,
                      actor: Actor = ANON) -> int:
        """Validated client attestations. Rejections are audited."""
        try:
            clean = validate_client_inputs(inputs)
            return self.led.apply_event(event_id, mission_id, expected_revision, clean,
                                        actor=actor.role, party=actor.id)
        except (InputError, ValueError) as exc:
            self._audit_reject(mission_id, actor, exc, {"inputs_keys": sorted(inputs) if isinstance(inputs, dict) else None})
            raise

    def _audit_reject(self, mission_id, actor: Actor, exc: Exception, payload: dict | None = None) -> None:
        with contextlib.suppress(Exception):  # never mask the original error
            self.led.audit_note(mission_id, actor.role, f"input rejected: {exc}",
                                {"actor": actor.id, "error": type(exc).__name__, **(payload or {})})

    def init_mission_facts(self, mission_id: str, event_id: str, scenario: str, location: str = "depot",
                           insured: bool = True) -> int:
        """Server-side initial facts for a console scenario (revision 0 -> 1)."""
        key, point = resolve_location(location)
        zones = zones_at(point)
        return self.led.apply_event(
            event_id, mission_id, 0,
            {"scenario": scenario, "insured": insured, "location": key,
             "prohibited_zone": bool(zones), "zones_entered": zones},
            actor="system", kind="input.event", summary=f"scenario {scenario} initialised at {key}")

    def set_location(self, mission_id: str, event_id: str, expected_revision: int, location: str,
                     actor: Actor = SIM_SENDER) -> int:
        """The server maps a named place to coordinates and runs the zone geometry itself."""
        try:
            key, point = resolve_location(location)
            m = self._mission(mission_id)
            cur = json.loads(m["inputs"])
            zones = zones_at(point)
            entered = sorted(set(cur.get("zones_entered") or []) | set(zones))
            patch = {"location": key, "zones_entered": entered}
            # never send False over a latched True: entering a zone is not undone by leaving it
            patch["prohibited_zone"] = bool(entered)
            return self.led.apply_event(event_id, mission_id, expected_revision, patch, actor="system",
                                        summary=f"location {key}; zones now {zones or 'none'}; entered {entered or 'none'}",
                                        party=actor.id,
                                        audit_extra={"point": list(point), "zones": zones, "source": "simulation"})
        except (InputError, ValueError) as exc:
            self._audit_reject(mission_id, actor, exc, {"location": str(location)})
            raise

    def _order(self, m) -> Order:
        o = self._orders.get(m["id"])
        if o is None:
            amt = m["amount_minor"]
            o = Order(id=m["id"], recipient_id=f"recipient:{m['id']}", assigned_driver=f"driver:{m['id']}",
                      assignment_version=1, destination=DESTINATION, merchant_destination="acct_sim_merchant",
                      driver_destination="acct_sim_driver", total_minor=amt, merchant_minor=amt,
                      driver_minor=0, retention_minor=0, currency=m["currency"].lower())
            self._orders[m["id"]] = o
        return o

    def issue_receipt_nonce(self, mission_id: str) -> str:
        """Recipient device asks for a one-use receipt nonce (expires; rate limited by proof.py)."""
        m = self._mission(mission_id)
        return self.nonces.issue(self._order(m), self.clock())

    def confirm_receipt(self, mission_id: str, event_id: str, expected_revision: int, *,
                        recipient_id: str | None = None, nonce: str | None = None) -> int:
        """receipt_confirmed is set only if proof.verify_proof passes: assigned driver, fresh
        position, inside the destination radius (position comes from the server-held sim
        location), and a one-use recipient nonce. Without a nonce the server plays the recipient
        device (labelled 'simulation' in the audit) but every other gate is still enforced."""
        existing = self.led.event_revision(event_id)
        if existing is not None:
            return existing
        m = self._mission(mission_id)
        inputs = json.loads(m["inputs"])
        order = self._order(m)
        now = self.clock()
        sim = nonce is None
        try:
            key, point = resolve_location(inputs.get("location", "depot"))
        except InputError as exc:
            self._audit_reject(mission_id, Actor("server", "system"), exc)
            raise
        if sim:
            recipient_id, nonce = order.recipient_id, self.nonces.issue(order, now)
        elif not recipient_id:
            raise InputError("recipient_required", "recipientId is required with a receipt nonce")
        res = verify_proof(order, self.nonces, driver_id=order.assigned_driver, position=point,
                           position_ts=now, recipient_id=recipient_id, nonce=nonce, now=now)
        source = "simulation" if sim else "recipient_device"
        self.led.audit_note(mission_id, "system" if sim else "receiver",
                                 "delivery proof " + ("accepted" if res.ok else "rejected"),
                                 {"gates": res.gates, "reasons": res.reasons, "source": source,
                                  "location": key, "rate_limited": res.rate_limited},
                                 kind="delivery.proof")
        if not res.ok:
            raise ProofRejected(res.gates, res.reasons)
        return self.led.apply_event(
            event_id, mission_id, expected_revision,
            {"receipt_confirmed": True, "receipt_proof": {"gates": res.gates, "source": source}},
            actor="system", summary=f"receipt confirmed by server-side proof ({source})",
            party=recipient_id, audit_extra={"gates": res.gates, "source": source})

    def set_amount(self, mission_id: str, event_id: str, expected_revision: int, amount_minor: int,
                   actor: Actor = ANON) -> int:
        try:
            return self.led.apply_event(event_id, mission_id, expected_revision, {}, actor=actor.role,
                                        party=actor.id, set_amount_minor=amount_minor,
                                        summary=f"amount change requested: {amount_minor}")
        except ValueError as exc:
            self._audit_reject(mission_id, actor, exc, {"requested_amount_minor": amount_minor})
            raise

    def attach_policy(self, mission_id: str, policy_id: str, version: int,
                      expected_target_version: int | None = None, actor: Actor = ANON) -> dict:
        reg = self.registry
        self._mission(mission_id)
        tv = reg.target_version("mission", mission_id) if expected_target_version is None else expected_target_version
        att = reg.attach("mission", mission_id, policy_id, version, tv)
        self.led.audit_note(mission_id, actor.role, f"policy {policy_id} v{version} attached to mission",
                                 {"attachment": att.__dict__, "actor": actor.id}, kind="policy.attach")
        return att.__dict__

    # ------------------------------------------------------------ evaluation
    def _mission(self, mission_id: str):
        m = self.led.mission(mission_id)
        if m is None:
            raise KeyError(mission_id)
        return m

    def _evaluate(self, mission_id: str) -> tuple[dict, dict]:
        """Pure evaluation (no writes). Returns (body, extras)."""
        m = self._mission(mission_id)
        reg = self.registry
        inputs = {**json.loads(m["inputs"]), "currency": m["currency"].lower()}
        inputs.update(server_facts(self.led.audit, mission_id, m["recipient"]))
        inherited = [("payment", f"pay:{mission_id}")]
        for key, ttype in (("job_id", "job"), ("card_id", "card"), ("counterparty_id", "counterparty")):
            if isinstance(inputs.get(key), str) and inputs[key]:
                inherited.append((ttype, inputs[key]))
        if m["recipient"]:
            inherited.append(("counterparty", m["recipient"]))
        eff = reg.decide("mission", mission_id, inputs, m["revision"], m["amount_minor"], inherited)

        approvals = [a for a in self.led.approvals(mission_id) if a["policy_hash"] == eff.effective_policy_hash]
        overrides = [o for o in self.led.overrides(mission_id) if o["policy_hash"] == eff.effective_policy_hash]
        valid_digest = approval_digest(mission_id, m["amount_minor"], m["currency"], m["recipient"],
                                       eff.effective_policy_hash)
        approval = next((a for a in reversed(approvals) if a["digest"] == valid_digest), None)

        results, approval_required, approvable = [], False, []
        for r in eff.results:
            r = dict(r)
            kind = self._clause_kind(reg, r)
            if r["outcome"] == "deny" and kind != "allowed_currency" and any(
                    o["policy_id"] == r.get("policy_id") and o["clause_id"] == r.get("clause") for o in overrides):
                o = next(o for o in overrides if o["policy_id"] == r.get("policy_id") and o["clause_id"] == r.get("clause"))
                r["evidence"] = {**r["evidence"], "overridden_by": o["approver"], "override_reason": o["reason"],
                                 "overridden_outcome": "deny"}
                r["outcome"] = "allow"
            elif r["outcome"] == "hold" and kind == "max_amount":
                approval_required = True
                if approval is not None:
                    r["evidence"] = {**r["evidence"], "approved_by": approval["approver"],
                                     "approval_id": approval["id"], "overridden_outcome": "hold"}
                    r["outcome"] = "allow"
                else:
                    approvable.append(r)
            results.append(r)

        # sticky deny
        latch = self.led.latch(mission_id)
        denies = [(r.get("policy_id"), r.get("clause")) for r in results if r["outcome"] == "deny"]
        sticky_clauses: list = []
        if latch and latch["policy_hash"] == eff.effective_policy_hash and not denies:
            overridden = {(o["policy_id"], o["clause_id"]) for o in overrides}
            sticky_clauses = [c for c in latch["clauses"] if tuple(c) not in overridden]
            if sticky_clauses:
                results.append({"policy_id": None, "clause": "STICKY-DENY", "outcome": "deny",
                                "evidence": {"latched_clauses": sticky_clauses, "since_revision": latch["revision"],
                                             "cleared_by": "new policy version or approver override only"}})
        verdict = "allow"
        for r in results:
            if RANK[r["outcome"]] > RANK[verdict]:
                verdict = r["outcome"]
        if not results:
            verdict = eff.verdict
        pids = list(dict.fromkeys(a[1] for a in eff.attachments))
        body = {
            "verdict": verdict, "policy_id": "+".join(pids) or "none",
            "policy_version": eff.attachments[0][2] if eff.attachments else 0,
            "policy_hash": eff.effective_policy_hash, "input_revision": m["revision"],
            "results": results, "mode": self.mode, "attachments": [list(a) for a in eff.attachments],
            "reasons": list(eff.reasons), "amount_minor": m["amount_minor"], "currency": m["currency"].lower(),
            "recipient": m["recipient"], "approval_required": approval_required,
            "approval": None if approval is None else {"id": approval["id"], "approver": approval["approver"]},
            "sticky": bool(sticky_clauses),
            "facts": {k: inputs.get(k) for k in ("receipt_confirmed", "location", "prohibited_zone", "insured")
                      if k in inputs},
        }
        body["decision_key"] = hashlib.sha256(json.dumps(
            [verdict, eff.effective_policy_hash, m["revision"], m["amount_minor"], results,
             body["approval"], [a[0] for a in eff.attachments]], sort_keys=True, default=str).encode()).hexdigest()
        return body, {"denies": denies, "approvable": approvable, "eff": eff, "mission": m}

    @staticmethod
    def _clause_kind(reg: PolicyRegistry, r: dict) -> str | None:
        if not r.get("clause") or not r.get("policy_id"):
            return None
        try:
            b = reg.get(r["policy_id"], r.get("policy_version"))
        except NotFoundError:
            return None
        return next((c.kind for c in b.clauses if c.id == r["clause"]), None)

    def evaluate(self, mission_id: str) -> dict:
        """Current effective decision without recording it."""
        return self._evaluate(mission_id)[0]

    def decide(self, mission_id: str, _snap: dict | None = None) -> dict:
        """Evaluate, latch denies, persist and audit (once per distinct decision)."""
        body, ex = self._evaluate(mission_id)
        m = ex["mission"]
        if ex["denies"]:
            latch = self.led.latch(mission_id)
            if latch is None or latch["policy_hash"] != body["policy_hash"]:
                self.led.set_latch(mission_id, body["policy_hash"], m["revision"], [list(d) for d in ex["denies"]])
        if self.led.record_decision(mission_id, m["revision"], body):
            self._audit_decision(mission_id, body)
        return body

    def _audit_decision(self, mission_id: str, body: dict) -> None:
        """One audit entry per distinct decision: verdict, every clause result with evidence,
        effective policy hash, facts used, approval/override state."""
        with self.led.transaction() as (_c, audit):
            audit.append(
                "system", "decision",
                f"decision {body['verdict']} at revision {body['input_revision']} under "
                f"{body['policy_id']} ({body['policy_hash']})",
                mission_id=mission_id, mode=body["mode"],
                policy={"id": body["policy_id"], "version": body["policy_version"], "hash": body["policy_hash"],
                        "verdict": body["verdict"], "clauses": body["results"]},
                payload={"revision": body["input_revision"], "facts": body["facts"],
                         "inputs": body["facts"],
                         "attachments": body["attachments"], "reasons": body["reasons"],
                         "amount_minor": body["amount_minor"], "currency": body["currency"],
                         "approval_required": body["approval_required"], "approval": body["approval"],
                         "sticky": body["sticky"], "decision_key": body["decision_key"]},
                commit=False)

    # ------------------------------------------------------------ approval and override
    def _require_role(self, actor: Actor, what: str) -> None:
        if actor.role not in APPROVER_ROLES:
            raise ApprovalError("forbidden", f"{what} requires an approver role (got {actor.role!r})", 403)

    def approve(self, mission_id: str, event_id: str, expected_revision: int, actor: Actor, *,
                amount_minor, currency, recipient, policy_hash) -> int:
        """Resolve an APPROVE-1 hold. The approver states exactly what they approve; it must
        equal the mission's current amount, currency, recipient and effective policy hash."""
        try:
            self._require_role(actor, "approval")
            body, ex = self._evaluate(mission_id)
            m = ex["mission"]
            if not ex["approvable"]:
                raise ApprovalError("nothing_to_approve", "no approval hold is pending on this mission")
            if any(r["outcome"] == "deny" for r in body["results"]):
                raise ApprovalError("denied", "a denied mission cannot be approved; use an audited override")
            want = {"amount_minor": m["amount_minor"], "currency": m["currency"].lower(),
                    "recipient": m["recipient"], "policy_hash": body["policy_hash"]}
            got = {"amount_minor": amount_minor, "currency": str(currency).lower(),
                   "recipient": recipient, "policy_hash": policy_hash}
            if isinstance(amount_minor, bool) or got != want:
                raise ApprovalError("approval_mismatch",
                                    f"approval must be bound to {want}; received {got}")
            digest = approval_digest(mission_id, want["amount_minor"], want["currency"], want["recipient"],
                                     want["policy_hash"])

            def extra(c, rev):
                c.execute("INSERT INTO approvals(mission_id,amount_minor,currency,recipient,policy_hash,approver,"
                          "role,revision,ts,digest) VALUES(?,?,?,?,?,?,?,?,datetime('now'),?)",
                          (mission_id, want["amount_minor"], want["currency"], recipient, want["policy_hash"],
                           actor.id, actor.role, rev, digest))

            return self.led.apply_event(
                event_id, mission_id, expected_revision, {"last_approval": digest[:16]}, actor=actor.role,
                kind="approval", party=actor.id, extra=extra,
                summary=f"{actor.id} approved {want['amount_minor']} {want['currency']} to {recipient}",
                audit_extra={"bound": want, "digest": digest})
        except (ApprovalError, ValueError) as exc:
            self._audit_reject(mission_id, actor, exc, {"kind": "approval"})
            raise

    def override(self, mission_id: str, event_id: str, expected_revision: int, actor: Actor, *,
                 clause_ids: list[str], reason: str, policy_hash: str) -> int:
        """Approver-only, audited, bound to the effective policy hash. Never covers CURRENCY clauses."""
        try:
            self._require_role(actor, "override")
            if not isinstance(reason, str) or not reason.strip():
                raise ApprovalError("reason_required", "an override needs a written reason", 422)
            if not clause_ids or not all(isinstance(c, str) for c in clause_ids):
                raise ApprovalError("bad_clauses", "clauseIds must be a non-empty list of strings", 422)
            body, ex = self._evaluate(mission_id)
            if policy_hash != body["policy_hash"]:
                raise ApprovalError("override_mismatch",
                                    f"override must be bound to effective policy hash {body['policy_hash']}")
            reg = self.registry
            latch = self.led.latch(mission_id)
            denied = {tuple(d) for d in ex["denies"]} | {tuple(c) for c in (latch["clauses"] if latch else [])}
            chosen = []
            for cid in clause_ids:
                match = [d for d in denied if d[1] == cid]
                if not match:
                    raise ApprovalError("not_denied", f"clause {cid} is not denying this mission")
                for pid, clause in match:
                    kind = self._clause_kind(reg, {"policy_id": pid, "clause": clause,
                                                   "policy_version": self._version_of(body, pid)})
                    if kind == "allowed_currency":
                        raise ApprovalError("not_overridable", "currency clauses cannot be overridden", 403)
                    chosen.append((pid, clause))

            def extra(c, rev):
                for pid, clause in chosen:
                    c.execute("INSERT INTO overrides(mission_id,policy_id,clause_id,policy_hash,approver,role,reason,"
                              "revision,ts) VALUES(?,?,?,?,?,?,?,?,datetime('now'))",
                              (mission_id, pid, clause, policy_hash, actor.id, actor.role, reason.strip(), rev))

            return self.led.apply_event(
                event_id, mission_id, expected_revision, {"last_override": event_id}, actor=actor.role,
                kind="override", party=actor.id, extra=extra,
                summary=f"{actor.id} overrode {sorted({c for _, c in chosen})}: {reason.strip()[:120]}",
                audit_extra={"clauses": [list(c) for c in chosen], "reason": reason.strip(),
                             "policy_hash": policy_hash})
        except (ApprovalError, ValueError) as exc:
            self._audit_reject(mission_id, actor, exc, {"kind": "override"})
            raise

    @staticmethod
    def _version_of(body: dict, policy_id: str) -> int | None:
        for a in body["attachments"]:
            if a[1] == policy_id:
                return a[2]
        return None


def is_proof_error(exc: Exception) -> bool:
    return isinstance(exc, ProofError)
