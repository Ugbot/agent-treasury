"""Audit pack: a deterministic, redacted zip a finance reviewer can keep and re-verify offline.

Contents: report.html (printable), audit.ndjson, decisions.json, policies.json, chain.json, README.txt.
Deterministic given the DB: sorted keys, fixed zip timestamps, no wall-clock values.
"""
from __future__ import annotations

import html
import io
import json
import re
import zipfile

from .audit import AuditLog, redact
from .bitemporal import PolicyTimeline

_FIXED = (2020, 1, 1, 0, 0, 0)


def _j(o) -> str:
    return json.dumps(redact(o), sort_keys=True, indent=2, default=str)


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


EVIDENCE_PREFIXES = ("decision", "agent.verdict", "delivery.proof", "settle.", "refund", "fuel.")
_STRIPE_RE = re.compile(r"\b(?:pi|ch|tr|po|re|in|iauth|ic|ipi)_[A-Za-z0-9_]+")


def _is_evidence(kind: str) -> bool:
    return any(kind == p or kind.startswith(p) for p in EVIDENCE_PREFIXES)


def _stripe_ids(entries) -> list[str]:
    ids: set[str] = set()
    for e in entries:
        ids.update((e.get("stripe") or {}).get("ids") or [])
        ids.update(_STRIPE_RE.findall(json.dumps(e.get("payload") or {}, default=str)))
    return sorted(ids)


def build_pack(audit: AuditLog, mission: str) -> bytes:
    entries = redact(audit.query(mission_id=mission, limit=1_000_000))
    if mission != "org":
        freezes = [e for e in redact(audit.query(mission_id="org", limit=1_000_000))
                   if str(e["kind"]).startswith("policy.freeze")]
        entries = sorted(entries + freezes, key=lambda e: e["seq"])
    decisions = [e for e in entries if _is_evidence(e["kind"])]
    tl = PolicyTimeline(audit)
    policies = []
    for d in decisions:
        hashes = (d.get("policy") or {})
        try:
            reg = tl.snapshot(d.get("valid_at") or d["ts"], basis="recorded", before_seq=d["seq"])
        except Exception:  # noqa: BLE001 - a bad snapshot must not break the pack
            reg = None
        pol = []
        if reg is not None:
            for pid, b in sorted((k if isinstance(k, str) else str(k), v) for k, v in
                                 getattr(reg, "bundles", {}).items()):
                bd = b.to_dict() if hasattr(b, "to_dict") else b
                pol.append({"policy_id": bd.get("policy_id"), "version": bd.get("version"),
                            "content_hash": bd.get("content_hash"), "clauses": bd.get("clauses")})
        policies.append({"decision_seq": d["seq"], "kind": d["kind"], "in_force": hashes.get("id"),
                         "version": hashes.get("version"), "effective_hash": hashes.get("hash"),
                         "bundles_known_then": pol})
    ver = audit.verify()
    chain = {"verify": ver, "head": ver.get("head"), "entries": ver.get("entries"),
             "mission_entries": len(entries),
             "reverify": "Recompute for each audit_log row in seq order: hash = sha256(prev_hash + canonical_json(row)) "
                         "where row = {ts,actor,party,mission_id,kind,summary,payload,policy,stripe,mode"
                         "[,valid_at][,supersedes]} with sorted keys and (',',':') separators, genesis prev_hash = 64 zeros. "
                         "Or call GET /api/audit/verify?expected_head=<head>&expected_count=<entries>."}
    stripe_ids = _stripe_ids(entries)
    report = _report(mission, entries, decisions, policies, chain, stripe_ids)
    ndjson = "".join(json.dumps(e, sort_keys=True, default=str) + "\n" for e in entries)
    readme = (f"Audit pack for mission {mission}\n\nreport.html  printable finance report\n"
              "audit.ndjson  audit entries for this mission (secrets redacted)\n"
              "decisions.json  every decision with clause results\n"
              "policies.json  policy versions and hashes in force at each decision\n"
              "chain.json  hash-chain verification result, head hash, how to re-verify\n\n"
              "Money moves only in Stripe TEST mode or MOCK mode; each payment is labelled.\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (("report.html", report), ("audit.ndjson", ndjson),
                           ("decisions.json", _j(decisions)), ("policies.json", _j(policies)),
                           ("chain.json", _j(chain)), ("README.txt", readme)):
            zi = zipfile.ZipInfo(name, _FIXED)
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
    return buf.getvalue()


def _report(mission, entries, decisions, policies, chain, stripe_ids) -> str:
    o = [(f"<!doctype html><meta charset=utf-8><title>Audit report {_e(mission)}</title>"
          "<style>body{font:14px/1.5 system-ui;max-width:900px;margin:2rem auto;color:#111}"
          "table{border-collapse:collapse;width:100%}td,th{border:1px solid #bbb;padding:4px 8px;text-align:left}"
          "h2{margin-top:2rem}code{background:#eee;padding:0 3px}@media print{body{margin:0}}</style>"),
         f"<h1>Audit report: mission {_e(mission)}</h1>"]
    modes = sorted({str(e.get("mode")) for e in entries if e.get("mode")})
    label = ", ".join("STRIPE TEST" if m == "stripe_test" else "MOCK" if m == "mock" else m.upper() for m in modes)
    pays = [e for e in entries if (e.get("stripe") or _stripe_ids([e])) and
            (e["kind"] in ("stripe.call", "payment", "saga.step") or _is_evidence(e["kind"]))]
    o.append(f"<h2>Summary</h2><p>{len(entries)} audit entries, {len(decisions)} decisions, "
             f"{len(stripe_ids)} Stripe object ids. Mode: {_e(label or 'n/a')}. "
             f"Chain integrity: <b>{'VERIFIED' if chain['verify'].get('ok') else 'BROKEN'}</b>.</p>")
    o.append("<h2>Decisions and policy in force</h2>")
    for d, p in zip(decisions, policies):
        pol = d.get("policy") or {}
        pl = d.get("payload") or {}
        o.append(f"<h3>Decision #{d['seq']}: {_e(str(pol.get('verdict', '')).upper())}</h3>"
                 f"<p>{_e(d['summary'])}</p><p>Policy <code>{_e(pol.get('id'))}</code> v{_e(pol.get('version'))}, "
                 f"effective hash <code>{_e(pol.get('hash'))}</code>; amount {_e(pl.get('amount_minor'))} "
                 f"{_e(pl.get('currency'))}; recorded {_e(d['ts'])}.</p>")
        if pl.get("reasons"):
            o.append("<p>Why: " + _e("; ".join(map(str, pl["reasons"]))) + "</p>")
        o.append("<table><tr><th>Clause</th><th>Outcome</th><th>Evidence</th></tr>")
        for c in pol.get("clauses") or []:
            if isinstance(c, dict):
                o.append(f"<tr><td>{_e(c.get('clause'))}</td><td>{_e(c.get('outcome'))}</td>"
                         f"<td><code>{_e(json.dumps(c.get('evidence', {}), sort_keys=True, default=str))}</code></td></tr>")
        o.append("</table>")
    o.append("<h2>Payments and Stripe objects</h2><table><tr><th>Seq</th><th>Mode</th><th>Stripe ids</th><th>Summary</th></tr>")
    for e in pays:
        m = e.get("mode")
        o.append(f"<tr><td>{e['seq']}</td><td>{'STRIPE TEST' if m == 'stripe_test' else 'MOCK' if m == 'mock' else _e(m)}</td>"
                 f"<td>{_e(', '.join(_stripe_ids([e])))}</td><td>{_e(e['summary'])}</td></tr>")
    o.append(f"</table><p>All Stripe object ids: {_e(', '.join(stripe_ids) or 'none')}</p>")
    o.append("<h2>Approvals</h2><ul>")
    for e in entries:
        if e["kind"] in ("approval", "override"):
            o.append(f"<li>#{e['seq']} {_e(e['actor'])}: {_e(e['summary'])}</li>")
    o.append("</ul><h2>Timeline</h2><table><tr><th>Seq</th><th>Time</th><th>Actor</th><th>Kind</th><th>Summary</th></tr>")
    for e in entries:
        o.append(f"<tr><td>{e['seq']}</td><td>{_e(e['ts'])}</td><td>{_e(e['actor'])}</td><td>{_e(e['kind'])}</td><td>{_e(e['summary'])}</td></tr>")
    o.append(f"</table><h2>Chain</h2><p>Head <code>{_e(chain['head'])}</code>, {_e(chain['entries'])} entries. "
             f"{_e(chain['reverify'])}</p>")
    return "\n".join(o)
