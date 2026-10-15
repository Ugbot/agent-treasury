"""Bitemporal policy history: replay any payment and see WHAT THE POLICY WAS AT THE TIME.

Two times per audit entry:
- transaction time: `audit_log.ts`, when the system recorded it (never changes);
- valid time: `audit_log.valid_at`, when it happened in the world (may be a simulated clock);
  falls back to `ts` when the writer did not supply one.

Policy publish/attach/detach are logged with the FULL bundle snapshot, so history never depends
on live registry state. Replays build a throwaway `PolicyRegistry` from those snapshots and call
its own `decide`, so semantics cannot drift from production. Corrections are new entries
(`supersedes`), history is never rewritten. See docs/BITEMPORAL.md.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from .audit import AuditLog
from .memory import MemoryWrite
from .policies import Attachment, NotFoundError, PolicyBundle, PolicyRegistry
from .policy import Clause

POLICY_KINDS = ("policy.publish", "policy.attach", "policy.detach")
_BIG = 1_000_000
_OVERRIDE_KEYS = ("overridden_by", "override_reason", "overridden_outcome", "approved_by", "approval_id")


# ----------------------------------------------------------------------------- helpers

def _dt(value) -> datetime:
    if isinstance(value, datetime):
        d = value
    else:
        d = datetime.fromisoformat(str(value))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _eff_valid(e: dict) -> str:
    return e.get("valid_at") or e["ts"]


def _bundle_snapshot(b: dict) -> dict:
    """Full, self-contained snapshot of a bundle (no live-registry or memory state)."""
    return {"policy_id": b["policy_id"], "version": b["version"], "title": b.get("title", ""),
            "content_hash": b["content_hash"],
            "clauses": [{"id": c["id"], "prose": c["prose"], "kind": c["kind"], "params": c["params"]}
                        for c in b["clauses"]]}


def _to_bundle(d: dict) -> PolicyBundle:
    clauses = tuple(Clause(c["id"], c["prose"], c["kind"], c.get("params") or {}) for c in d["clauses"])
    return PolicyBundle(d["policy_id"], d["version"], d.get("title", ""), clauses, d["content_hash"],
                        MemoryWrite(True, "snapshot", "restored from audit log"))


def _norm(x):
    return json.loads(json.dumps(x, sort_keys=True, default=str))


# ----------------------------------------------------------------------------- recording

def record_policy_event(audit: AuditLog, kind: str, bundle_dict: dict, target: dict | None = None,
                        valid_at: str | None = None, actor: str = "system") -> int:
    """Append a policy.publish / policy.attach / policy.detach entry carrying the full bundle
    snapshot. `target` holds attachment details {attachment_id, target_type, target_id,
    policy_id, policy_version, mandatory}."""
    if kind not in POLICY_KINDS:
        raise ValueError(f"kind must be one of {POLICY_KINDS}")
    snap = _bundle_snapshot(bundle_dict)
    label = f"{snap['policy_id']} v{snap['version']}"
    if kind == "policy.publish":
        summary = f"policy {label} published ({len(snap['clauses'])} clauses, hash {snap['content_hash'][:12]})"
    else:
        t = target or {}
        where = f"{t.get('target_type')} {t.get('target_id')}"
        verb = "attached to" if kind == "policy.attach" else "detached from"
        summary = f"policy {label} {verb} {where}" + (" (mandatory)" if t.get("mandatory") else "")
    return audit.append(actor, kind, summary, payload={"bundle": snap, "attachment": target},
                        valid_at=valid_at)


def replay_registry(registry: PolicyRegistry, audit: AuditLog) -> int:
    """Restore published bundles (and their versions) from policy.publish audit entries after a
    restart. Skips versions already present, out-of-order versions and any bundle whose content
    hash does not verify. Does not re-audit or touch memory. Returns the number restored."""
    n = 0
    with registry._lock:
        for e in sorted(audit.query(kind="policy.publish", limit=_BIG), key=lambda e: e["seq"]):
            try:
                b = _to_bundle(e["payload"]["bundle"])
            except (KeyError, TypeError, ValueError):
                continue
            have = registry._versions.get(b.policy_id, [])
            if b.version != len(have) + 1 or not b.verify():
                continue
            registry._versions.setdefault(b.policy_id, []).append(b)
            n += 1
    return n


def instrument_registry(registry: PolicyRegistry, audit: AuditLog, *, clock=None, actor: str = "system"):
    """Wrap publish/attach/detach on this registry instance so each emits an audit entry.

    Behaviour and return values are unchanged. Existing registry content (e.g. the seeded
    org-base policy) is backfilled once, deduplicated against what the log already holds.
    `clock` is an optional zero-arg callable returning the ISO valid time for new events.
    Returns the registry."""
    if getattr(registry, "_bitemporal_instrumented", False):
        return registry
    registry._bitemporal_instrumented = True
    now = clock or (lambda: None)

    known_pub = {(e["payload"]["bundle"]["policy_id"], e["payload"]["bundle"]["version"],
                  e["payload"]["bundle"]["content_hash"]) for e in audit.query(kind="policy.publish", limit=_BIG)}
    known_att = {_att_key(e["payload"]["attachment"]) for e in audit.query(kind="policy.attach", limit=_BIG)
                 if e["payload"].get("attachment")}
    for versions in list(registry._versions.values()):
        for b in versions:
            if (b.policy_id, b.version, b.content_hash) not in known_pub:
                record_policy_event(audit, "policy.publish", b.to_dict(), actor=actor, valid_at=now())
    for a in list(registry._attach.values()):
        if _att_key(a.__dict__) not in known_att:
            record_policy_event(audit, "policy.attach", registry.get(a.policy_id, a.policy_version).to_dict(),
                                a.__dict__, valid_at=now(), actor=actor)

    orig_publish, orig_attach, orig_detach = registry.publish, registry.attach, registry.detach

    def publish(*args, **kwargs):
        b = orig_publish(*args, **kwargs)
        record_policy_event(audit, "policy.publish", b.to_dict(), valid_at=now(), actor=actor)
        return b

    def attach(*args, **kwargs):
        att = orig_attach(*args, **kwargs)
        record_policy_event(audit, "policy.attach", registry.get(att.policy_id, att.policy_version).to_dict(),
                            dict(att.__dict__), valid_at=now(), actor=actor)
        return att

    def detach(attachment_id, *args, **kwargs):
        before = registry._attach.get(attachment_id)
        out = orig_detach(attachment_id, *args, **kwargs)
        if before is not None:
            record_policy_event(audit, "policy.detach",
                                registry.get(before.policy_id, before.policy_version).to_dict(),
                                dict(before.__dict__), valid_at=now(), actor=actor)
        return out

    registry.publish, registry.attach, registry.detach = publish, attach, detach
    return registry


def _att_key(a: dict) -> tuple:
    return (a["attachment_id"], a["target_type"], a["target_id"], a["policy_id"], a["policy_version"],
            bool(a["mandatory"]))


# ----------------------------------------------------------------------------- snapshots

class SnapshotRegistry(PolicyRegistry):
    """Throwaway registry rebuilt from logged snapshots. Lookups are by (id, version), so gaps in
    the log cannot shift versions. All decision logic is inherited from PolicyRegistry."""

    def __init__(self):
        super().__init__()
        self.bundles: dict[tuple[str, int], PolicyBundle] = {}

    def get(self, policy_id: str, version: int | None = None) -> PolicyBundle:
        vs = [v for (pid, v) in self.bundles if pid == policy_id]
        if not vs:
            raise NotFoundError(policy_id)
        v = max(vs) if version is None else version
        if (policy_id, v) not in self.bundles:
            raise NotFoundError(f"{policy_id} v{v}")
        return self.bundles[(policy_id, v)]

    def add_bundle(self, d: dict) -> None:
        self.bundles.setdefault((d["policy_id"], d["version"]), _to_bundle(d))

    def add_attachment(self, a: dict) -> None:
        for x in [x for x in self._attach.values()
                  if (x.target_type, x.target_id, x.policy_id) == (a["target_type"], a["target_id"], a["policy_id"])]:
            del self._attach[x.attachment_id]
        self._attach[a["attachment_id"]] = Attachment(
            a["attachment_id"], a["target_type"], a["target_id"], a["policy_id"], a["policy_version"],
            bool(a["mandatory"]))

    def remove_attachment(self, a: dict) -> None:
        cur = self._attach.get(a["attachment_id"])
        if cur and (cur.target_type, cur.target_id) == (a["target_type"], a["target_id"]):
            del self._attach[a["attachment_id"]]


def effective_hash(atts, reg: PolicyRegistry) -> str:
    """Same formula as PolicyRegistry.decide's effective_policy_hash."""
    parts = []
    for a in atts:
        try:
            parts.append((a.policy_id, a.policy_version, reg.get(a.policy_id, a.policy_version).content_hash))
        except NotFoundError:
            pass
    return hashlib.sha256(json.dumps(sorted(parts)).encode()).hexdigest()[:16]


class PolicyTimeline:
    def __init__(self, audit: AuditLog):
        self.audit = audit

    def events(self) -> list[dict]:
        out = []
        for k in POLICY_KINDS:
            out.extend(self.audit.query(kind=k, limit=_BIG))
        return sorted(out, key=lambda e: e["seq"])

    def snapshot(self, at, basis: str = "valid", *, before_seq: int | None = None) -> SnapshotRegistry:
        """Registry as it stood at `at`.
        basis 'valid': events whose valid time <= at, replayed in valid-time order (what was
          really in force, including retroactive corrections recorded later).
        basis 'recorded': events recorded (transaction time) <= at, replayed in seq order (what
          the system knew then). `before_seq` instead cuts the log at a sequence number."""
        if basis not in ("valid", "recorded"):
            raise ValueError("basis must be 'valid' or 'recorded'")
        evs = self.events()
        if before_seq is not None:
            evs = [e for e in evs if e["seq"] < before_seq]
        elif basis == "valid":
            cut = _dt(at)
            evs = sorted((e for e in evs if _dt(_eff_valid(e)) <= cut), key=lambda e: (_dt(_eff_valid(e)), e["seq"]))
        else:
            cut = _dt(at)
            evs = [e for e in evs if _dt(e["ts"]) <= cut]
        reg = SnapshotRegistry()
        for e in evs:
            p = e["payload"]
            reg.add_bundle(p["bundle"])
            if e["kind"] == "policy.attach" and p.get("attachment"):
                reg.add_attachment(p["attachment"])
            elif e["kind"] == "policy.detach" and p.get("attachment"):
                reg.remove_attachment(p["attachment"])
        return reg

    def policy_as_of(self, target_type: str, target_id: str, at, basis: str = "valid",
                     inherited: list[tuple[str, str]] | None = None) -> dict:
        reg = self.snapshot(at, basis)
        return describe_in_force(reg, target_type, target_id, inherited, at=str(at), basis=basis)


def describe_in_force(reg: PolicyRegistry, target_type: str, target_id: str,
                      inherited: list[tuple[str, str]] | None, *, at: str, basis: str) -> dict:
    atts = reg.effective_attachments([(target_type, target_id), *(inherited or [])])
    pols, rows = [], []
    for a in atts:
        try:
            b = reg.get(a.policy_id, a.policy_version)
        except NotFoundError:
            rows.append({"attachment_id": a.attachment_id, "policy_id": a.policy_id,
                         "version": a.policy_version, "mandatory": a.mandatory, "hash": None,
                         "missing_snapshot": True})
            continue
        rows.append({"attachment_id": a.attachment_id, "target": f"{a.target_type}:{a.target_id}",
                     "policy_id": a.policy_id, "version": a.policy_version, "mandatory": a.mandatory,
                     "hash": b.content_hash, "title": b.title})
        pols.append({**_bundle_snapshot(b.to_dict())})
    if rows:
        names = ", ".join(f"{r['policy_id']} v{r['version']}" + (" (mandatory)" if r["mandatory"] else "")
                          for r in rows)
        summary = f"As of {at} ({basis} time), {len(rows)} polic{'y' if len(rows) == 1 else 'ies'} applied to " \
                  f"{target_type} {target_id}: {names}."
    else:
        summary = f"As of {at} ({basis} time), no policy was attached to {target_type} {target_id}; " \
                  "payments would be held."
    return {"target": {"type": target_type, "id": target_id}, "at": at, "basis": basis,
            "attachments": rows, "policies": pols, "effective_policy_hash": effective_hash(atts, reg),
            "summary": summary}


# ----------------------------------------------------------------------------- replay

def _decision_inputs(e: dict) -> dict:
    p = e.get("payload") or {}
    if isinstance(p.get("inputs"), dict):
        return dict(p["inputs"])
    return {**(p.get("facts") or {}), "currency": p.get("currency")}


def _raw_results(results: list[dict]) -> list[dict]:
    """Strip approval/override/sticky annotations so recorded results compare with a fresh evaluation."""
    out = []
    for r in results or []:
        if r.get("clause") == "STICKY-DENY":
            continue
        ev = dict(r.get("evidence") or {})
        outcome = ev.get("overridden_outcome", r["outcome"])
        for k in _OVERRIDE_KEYS:
            ev.pop(k, None)
        out.append(_norm({"policy_id": r.get("policy_id"), "clause": r.get("clause"),
                          "outcome": outcome, "evidence": ev}))
    return out


def _fresh_results(eff) -> list[dict]:
    return [_norm({"policy_id": r.get("policy_id"), "clause": r.get("clause"),
                   "outcome": r["outcome"], "evidence": r.get("evidence") or {}}) for r in eff.results]


def reevaluate(decision_entry: dict, snapshot: PolicyRegistry) -> dict:
    """Pure function of (policy snapshot, recorded inputs, revision, amount, recorded attachments).

    Rebuilds the recorded attachment set over the snapshot's bundles and calls PolicyRegistry.decide.
    `reproduced` is True when the clause outcomes and evidence equal the recorded ones (approval,
    override and sticky-deny annotations stripped, since those are ledger state, not policy)."""
    p = decision_entry.get("payload") or {}
    mid = decision_entry.get("mission_id") or "unknown"
    reg = SnapshotRegistry()
    reg.bundles = dict(snapshot.bundles)
    for att_id, pid, ver, mandatory in p.get("attachments") or []:
        reg.add_attachment({"attachment_id": att_id, "target_type": "organisation" if mandatory else "mission",
                            "target_id": "org" if mandatory else mid, "policy_id": pid,
                            "policy_version": ver, "mandatory": bool(mandatory)})
    eff = reg.decide("mission", mid, _decision_inputs(decision_entry), p.get("revision", 0),
                     p.get("amount_minor", 0))
    fresh = _fresh_results(eff)
    recorded = _raw_results((decision_entry.get("policy") or {}).get("clauses") or [])
    mism = [{"recorded": r, "recomputed": f} for r, f in zip(recorded, fresh) if r != f]
    if len(recorded) != len(fresh):
        mism.append({"recorded_clauses": len(recorded), "recomputed_clauses": len(fresh)})
    return {"verdict": eff.verdict, "results": list(eff.results), "reasons": list(eff.reasons),
            "effective_policy_hash": eff.effective_policy_hash, "reproduced": not mism, "mismatches": mism}


def reevaluate_recorded(audit: AuditLog, decision_entry: dict) -> dict:
    """Reproduce a stored decision from the policy snapshot as the log knew it just before it."""
    return reevaluate(decision_entry, PolicyTimeline(audit).snapshot(None, before_seq=decision_entry["seq"]))


def _why(policy_id, clause, then_b: PolicyBundle | None, now_b: PolicyBundle | None) -> str:
    def find(b):
        return next((c for c in b.clauses if c.id == clause), None) if b else None
    t, n = find(then_b), find(now_b)
    if t and not n:
        return f"clause {clause} of {policy_id} no longer applies (policy detached or clause removed)"
    if n and not t:
        return f"clause {clause} of {policy_id} is newly in force (v{now_b.version})"
    if t.kind == "max_amount" and t.params != n.params:
        a, b = t.params["limit_minor"], n.params["limit_minor"]
        return f"limit {'lowered' if b < a else 'raised'} from {a} to {b} minor units ({policy_id} v{then_b.version} -> v{now_b.version})"
    if t.params != n.params:
        return f"{clause} parameters changed {t.params} -> {n.params} ({policy_id} v{then_b.version} -> v{now_b.version})"
    return f"same rule under {policy_id} v{now_b.version}; outcome differs because of other policy changes"


def _bundle_at(reg, pid, ver_map):
    try:
        return reg.get(pid, ver_map[pid]) if pid in ver_map else None
    except NotFoundError:
        return None


def compare_decision(audit_entry: dict, then_reg: PolicyRegistry, now_reg: PolicyRegistry,
                     inherited: list[tuple[str, str]] | None = None) -> dict:
    """Recorded decision vs a re-evaluation under the `now_reg` chain."""
    p = audit_entry.get("payload") or {}
    mid = audit_entry.get("mission_id") or "unknown"
    recorded = (audit_entry.get("policy") or {}).get("verdict")
    eff = now_reg.decide("mission", mid, _decision_inputs(audit_entry), p.get("revision", 0),
                         p.get("amount_minor", 0), inherited or [])
    then_raw = {(r["policy_id"], r["clause"]): r for r in _raw_results(
        (audit_entry.get("policy") or {}).get("clauses") or [])}
    now_raw = {(r.get("policy_id"), r.get("clause")): r for r in eff.results}
    then_versions = {(pid): ver for _a, pid, ver, _m in p.get("attachments") or []}
    now_versions = {a[1]: a[2] for a in eff.attachments}
    diffs = []
    for key in list(dict.fromkeys([*then_raw, *now_raw])):
        to = then_raw.get(key, {}).get("outcome")
        no = now_raw.get(key, {}).get("outcome")
        if to == no:
            continue
        pid, clause = key
        diffs.append({"policy_id": pid, "clause": clause, "then": to, "now": no,
                      "why": _why(pid, clause, _bundle_at(then_reg, pid, then_versions),
                                  _bundle_at(now_reg, pid, now_versions))})
    diverged = recorded != eff.verdict
    if diverged:
        why = "; ".join(f"{d['clause']}: {d['why']}" for d in diffs) or "policy set changed"
        summary = f"{'Allowed' if recorded == 'allow' else recorded.capitalize()} under the policy at the time; " \
                  f"would be {eff.verdict} under current policy because {why}."
    else:
        summary = f"Same verdict ({recorded}) under the policy at the time and the current policy."
    return {"decision_seq": audit_entry["seq"], "recorded_verdict": recorded,
            "allowed_then": recorded == "allow", "would_now": eff.verdict,
            "would_now_allow": eff.verdict == "allow", "diverged": diverged,
            "differing_clauses": diffs, "then_policy_hash": (audit_entry.get("policy") or {}).get("hash"),
            "now_policy_hash": eff.effective_policy_hash, "summary": summary}


# ----------------------------------------------------------------------------- payment history

CATEGORY = {"input.event": "input", "input.rejected": "input", "delivery.proof": "input", "fuel.auth": "input",
            "fuel.issue": "input", "mission.created": "input", "decision": "decision", "approval": "approval",
            "override": "approval", "agent.proposal": "agent", "agent.verdict": "agent", "plan": "agent",
            "replan": "agent", "stripe.call": "stripe"}


def resolve_mission(audit: AuditLog, ident: str, ledger=None) -> str:
    if audit.query(mission_id=ident, limit=1):
        return ident
    if ledger is not None:
        rows = ledger._read("SELECT mission_id FROM payments WHERE operation_id=?", (ident,))
        if rows:
            return rows[0]["mission_id"]
    for r in audit.db.execute("SELECT mission_id FROM audit_log WHERE mission_id IS NOT NULL AND "
                              "(payload LIKE ? OR stripe LIKE ?) ORDER BY seq LIMIT 1",
                              (f"%{ident}%", f"%{ident}%")).fetchall():
        return r[0]
    return ident


def payment_history(audit: AuditLog, mission_or_payment_id: str, *, ledger=None,
                    current: PolicyRegistry | None = None, now=None) -> dict:
    """Ordered replay of one payment: inputs, decisions, approvals, agent proposals and Stripe
    calls, each with both times and the policy in force at its valid time, plus divergence
    between each recorded decision and a re-evaluation under CURRENT policy (the latest state
    in the log, or `current` when a live registry is given)."""
    mid = resolve_mission(audit, mission_or_payment_id, ledger)
    tl = PolicyTimeline(audit)
    entries = [e for e in audit.query(mission_id=mid, limit=_BIG) if e["kind"] not in POLICY_KINDS]
    now_reg = current or tl.snapshot(now or datetime.now(UTC).isoformat(), "recorded")
    inherited: list[tuple[str, str]] = [("payment", f"pay:{mid}")]
    items, max_valid, late = [], None, []
    for e in entries:
        patch = (e.get("payload") or {}).get("patch") or {}
        for key, ttype in (("job_id", "job"), ("card_id", "card"), ("counterparty_id", "counterparty")):
            if isinstance(patch.get(key), str) and patch[key] and (ttype, patch[key]) not in inherited:
                inherited.append((ttype, patch[key]))
        rcp = (e.get("payload") or {}).get("recipient")
        if e["kind"] == "mission.created" and rcp and ("counterparty", rcp) not in inherited:
            inherited.append(("counterparty", rcp))
        v = _eff_valid(e)
        is_late = max_valid is not None and _dt(v) < max_valid[0]
        if max_valid is None or _dt(v) > max_valid[0]:
            max_valid = (_dt(v), e["seq"])
        in_force = tl.policy_as_of("mission", mid, v, "valid", list(inherited))
        item = {"seq": e["seq"], "kind": e["kind"], "category": CATEGORY.get(e["kind"], "other"),
                "actor": e["actor"], "summary": e["summary"], "recorded_at": e["ts"], "valid_at": v,
                "valid_time_source": "explicit" if e.get("valid_at") else "recorded_time",
                "late_arrival": is_late, "supersedes": e.get("supersedes"),
                "policy_in_force": {"effective_policy_hash": in_force["effective_policy_hash"],
                                    "policies": [{"policy_id": a["policy_id"], "version": a["version"],
                                                  "hash": a["hash"], "mandatory": a["mandatory"]}
                                                 for a in in_force["attachments"]],
                                    "summary": in_force["summary"]}}
        if e["kind"] == "decision":
            item["verdict"] = (e.get("policy") or {}).get("verdict")
            item["recorded_policy_hash"] = (e.get("policy") or {}).get("hash")
            item["reproduced"] = reevaluate_recorded(audit, e)["reproduced"]
            item["divergence"] = compare_decision(e, tl.snapshot(None, before_seq=e["seq"]), now_reg, list(inherited))
        if is_late:
            late.append({"seq": e["seq"], "happened_at": v, "recorded_at": e["ts"],
                         "note": f"seq {e['seq']} was recorded after later-happening events but happened earlier "
                                 f"({v}); history was not rewritten"})
        items.append(item)
    decisions = [i for i in items if i["kind"] == "decision"]
    return {"mission_id": mid, "requested_id": mission_or_payment_id, "timeline": items,
            "orderings": {"by_transaction_time": [i["seq"] for i in items],
                          "by_valid_time": [i["seq"] for i in sorted(items, key=lambda i: (_dt(i["valid_at"]), i["seq"]))]},
            "late_arrivals": late,
            "divergence": decisions[-1]["divergence"] if decisions else None,
            "summary": f"{len(items)} events for {mid}; {len(decisions)} decisions; {len(late)} late arrivals."}
