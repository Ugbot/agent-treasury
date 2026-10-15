"""Append-only, hash-chained audit log: the record of everything and every decision.

Canonical store for explanations. Writers call `AuditLog.append(...)`; readers use `query`.
Entries are redacted at write time so secrets never reach the log, Cognee or the browser.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  actor TEXT NOT NULL, party TEXT, mission_id TEXT, kind TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL DEFAULT '{}',
  policy TEXT, stripe TEXT, mode TEXT, prev_hash TEXT NOT NULL, hash TEXT NOT NULL,
  valid_at TEXT, supersedes INTEGER);
CREATE INDEX IF NOT EXISTS audit_mission ON audit_log(mission_id, seq);
CREATE INDEX IF NOT EXISTS audit_party ON audit_log(party, seq);
"""

_SECRET = re.compile(r"\b(?:sk|rk|rkcs|pk|whsec)_(?:test_|live_)?[A-Za-z0-9_+/=-]{6,}")
GENESIS = "0" * 64

ACTORS = ("system", "agent", "planner", "sender", "receiver", "driver", "approver", "stripe", "cognee")
# Suggested kinds (free-form, keep them stable): input.event, policy.publish, policy.attach,
# decision, plan, replan, agent.proposal, agent.verdict, approval, stripe.call, saga.step,
# saga.compensation, memory.write, memory.read, chat.explain, delivery.proof, fuel.auth, refund.


def redact(value):
    if isinstance(value, str):
        return _SECRET.sub("[REDACTED]", value)
    if isinstance(value, dict):
        return {redact(str(k)): redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class AuditLog:
    def __init__(self, db):
        """db: sqlite3 connection (use Ledger.db so writes share the ledger's transaction)."""
        self.db = db
        self.db.executescript(SCHEMA)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(audit_log)").fetchall()}
        for col, typ in (("valid_at", "TEXT"), ("supersedes", "INTEGER")):  # migrate older DBs
            if col not in have:
                self.db.execute(f"ALTER TABLE audit_log ADD COLUMN {col} {typ}")

    def append(self, actor: str, kind: str, summary: str = "", *, mission_id: str | None = None,
               party: str | None = None, payload: dict | None = None, policy: dict | None = None,
               stripe: dict | None = None, mode: str | None = None, commit: bool = True,
               valid_at: str | None = None, supersedes: int | None = None) -> int:
        """policy: {id, version, hash, verdict, clauses:[...]}; stripe: {ids:[...], livemode, op}.

        Bitemporal: `ts` is transaction time (when recorded). `valid_at` is ISO valid time (when it
        happened in the world, may be a simulated clock). `supersedes` is the seq of an entry this
        one corrects; corrections are new entries, never edits. Both are hashed only when set so
        chains written before they existed still verify."""
        row = {"ts": datetime.now(UTC).isoformat(), "actor": actor, "party": party,
               "mission_id": mission_id, "kind": kind, "summary": redact(summary),
               "payload": redact(payload or {}), "policy": redact(policy) if policy else None,
               "stripe": redact(stripe) if stripe else None, "mode": mode}
        if valid_at is not None:
            row["valid_at"] = valid_at
        if supersedes is not None:
            row["supersedes"] = supersedes
        last = self.db.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        prev = last[0] if last else GENESIS
        h = hashlib.sha256((prev + _canon(row)).encode()).hexdigest()
        cur = self.db.execute(
            "INSERT INTO audit_log(ts,actor,party,mission_id,kind,summary,payload,policy,stripe,mode,prev_hash,hash,valid_at,supersedes)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["ts"], actor, party, mission_id, kind, row["summary"], _canon(row["payload"]),
             _canon(row["policy"]) if row["policy"] else None,
             _canon(row["stripe"]) if row["stripe"] else None, mode, prev, h, valid_at, supersedes))
        if commit:
            self.db.commit()
        return cur.lastrowid

    def query(self, *, mission_id=None, party=None, kind=None, actor=None, after=0, limit=500) -> list[dict]:
        where, args = ["seq > ?"], [after]
        for col, val in (("mission_id", mission_id), ("party", party), ("kind", kind), ("actor", actor)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        rows = self.db.execute(
            f"SELECT * FROM audit_log WHERE {' AND '.join(where)} ORDER BY seq LIMIT ?", (*args, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else dict(zip([c[0] for c in self.db.execute("SELECT * FROM audit_log LIMIT 0").description], r))
            for k in ("payload", "policy", "stripe"):
                d[k] = json.loads(d[k]) if d.get(k) else None
            out.append(d)
        return out

    def verify(self, expected_head: str | None = None, expected_count: int | None = None) -> dict:
        """Walk the chain. Returns head hash and entry count. Truncation (tail deleted) leaves a
        valid chain, so it is only detectable against an externally anchored head/count: pass
        expected_head / expected_count (the UI shows the head hash so it can be recorded elsewhere).
        A mismatch returns ok False with anchor_mismatch listing what differed."""
        res = self._walk()
        if not res["ok"]:
            return res
        mism = []
        if expected_count is not None and res["entries"] != expected_count:
            mism.append(f"entry count {res['entries']} != expected {expected_count}")
        if expected_head is not None and res["head"] != expected_head:
            mism.append("head hash differs from the anchored head (log truncated, rewritten or extended)")
        if expected_head is not None or expected_count is not None:
            res["anchor_checked"] = True
        if mism:
            res.update(ok=False, anchor_mismatch=mism)
        return res

    def _walk(self) -> dict:
        prev, n = GENESIS, 0
        for r in self.db.execute("SELECT * FROM audit_log ORDER BY seq").fetchall():
            d = dict(r)
            row = {"ts": d["ts"], "actor": d["actor"], "party": d["party"], "mission_id": d["mission_id"],
                   "kind": d["kind"], "summary": d["summary"],
                   "payload": json.loads(d["payload"]) if d["payload"] else {},
                   "policy": json.loads(d["policy"]) if d["policy"] else None,
                   "stripe": json.loads(d["stripe"]) if d["stripe"] else None, "mode": d["mode"]}
            if d.get("valid_at") is not None:
                row["valid_at"] = d["valid_at"]
            if d.get("supersedes") is not None:
                row["supersedes"] = d["supersedes"]
            if d["prev_hash"] != prev or d["hash"] != hashlib.sha256((prev + _canon(row)).encode()).hexdigest():
                return {"ok": False, "broken_at_seq": d["seq"], "entries": n}
            prev, n = d["hash"], n + 1
        return {"ok": True, "entries": n, "head": prev}
