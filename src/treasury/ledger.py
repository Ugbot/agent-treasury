"""SQLite ledger: events, missions, decisions, approvals and one-effect-per-operation payments.

Safety properties enforced here (not left to callers):
- apply_event is one atomic transaction (BEGIN IMMEDIATE on its own connection): event row,
  mission inputs, revision bump, any `extra` rows and the audit entry commit together or not at all.
- UNIQUE(mission_id, revision) on events and a compare-and-swap on missions.revision mean two
  concurrent events can never both land on the same revision, so no safety input is lost.
- Latched facts (a prohibited-zone entry) can never be cleared through an input patch.
- The mission amount is immutable once an approval hold or any payment exists.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

from .audit import AuditLog, redact

SCHEMA = """
CREATE TABLE IF NOT EXISTS missions(
  id TEXT PRIMARY KEY, amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
  currency TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
  inputs TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'open',
  recipient TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS events(
  id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_events_mission_revision ON events(mission_id, revision);
CREATE TABLE IF NOT EXISTS decisions(
  mission_id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL,
  PRIMARY KEY(mission_id, revision));
CREATE TABLE IF NOT EXISTS decision_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL, revision INTEGER NOT NULL,
  key TEXT NOT NULL, approval_required INTEGER NOT NULL DEFAULT 0, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_decision_history_mission ON decision_history(mission_id, id);
CREATE TABLE IF NOT EXISTS payments(
  operation_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, amount_minor INTEGER NOT NULL,
  currency TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('mock','stripe_test')),
  provider_ref TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL, amount_minor INTEGER NOT NULL,
  currency TEXT NOT NULL, recipient TEXT NOT NULL, policy_hash TEXT NOT NULL,
  approver TEXT NOT NULL, role TEXT NOT NULL, revision INTEGER NOT NULL, ts TEXT NOT NULL,
  digest TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_approvals_mission ON approvals(mission_id);
CREATE TABLE IF NOT EXISTS overrides(
  id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL, policy_id TEXT NOT NULL,
  clause_id TEXT NOT NULL, policy_hash TEXT NOT NULL, approver TEXT NOT NULL, role TEXT NOT NULL,
  reason TEXT NOT NULL, revision INTEGER NOT NULL, ts TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_overrides_mission ON overrides(mission_id);
CREATE TABLE IF NOT EXISTS deny_latch(
  mission_id TEXT PRIMARY KEY, policy_hash TEXT NOT NULL, revision INTEGER NOT NULL,
  clauses TEXT NOT NULL, ts TEXT NOT NULL);
"""

# Facts that, once true, can only be cleared by a policy change or an audited override.
LATCHED_KEYS = frozenset({"prohibited_zone"})


class StaleRevisionError(ValueError):
    """expected_revision does not match (HTTP 409)."""


class LatchedFactError(ValueError):
    """A latched safety fact cannot be cleared by an input event (HTTP 409)."""


class AmountLockedError(ValueError):
    """The mission amount is immutable after an approval hold or a payment (HTTP 409)."""


def approval_digest(mission_id: str, amount_minor: int, currency: str, recipient: str,
                    policy_hash: str) -> str:
    return hashlib.sha256(
        f"{mission_id}|{amount_minor}|{currency.lower()}|{recipient}|{policy_hash}".encode()).hexdigest()


def _now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


class Ledger:
    def __init__(self, path: str | Path = "treasury.sqlite"):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self._lock = threading.RLock()  # only used for in-memory databases
        if self.path != ":memory:":
            with contextlib.suppress(sqlite3.DatabaseError):
                self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(missions)")}
        if "recipient" not in cols:  # migrate databases created before approvals existed
            self.db.execute("ALTER TABLE missions ADD COLUMN recipient TEXT NOT NULL DEFAULT ''")
            self.db.commit()
        self.audit = AuditLog(self.db)

    # ------------------------------------------------------------ transactions
    @contextlib.contextmanager
    def transaction(self):
        """One atomic write transaction. File DBs use a private connection so concurrent
        threads cannot commit each other's half-finished work. Yields (conn, audit)."""
        if self.path == ":memory:":
            with self._lock:
                if self.db.in_transaction:
                    self.db.commit()
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    yield self.db, self.audit
                except BaseException:
                    self.db.rollback()
                    raise
                else:
                    self.db.commit()
            return
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            audit = AuditLog.__new__(AuditLog)  # table already created in __init__
            audit.db = conn
            try:
                yield conn, audit
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()

    # ------------------------------------------------------------ missions
    def create_mission(self, mission_id: str, amount_minor: int, currency: str,
                       recipient: str | None = None) -> None:
        if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
            raise ValueError("amount must be positive integer minor units")
        with self.transaction() as (c, audit):
            cur = c.execute(
                "INSERT OR IGNORE INTO missions(id, amount_minor, currency, recipient) VALUES(?,?,?,?)",
                (mission_id, amount_minor, currency.upper(), recipient or f"recipient:{mission_id}"),
            )
            if cur.rowcount == 1:
                audit.append("system", "mission.created",
                             f"mission {mission_id} created for {amount_minor} {currency.lower()} minor units",
                             mission_id=mission_id, party="sender",
                             payload={"amount_minor": amount_minor, "currency": currency.lower(),
                                      "recipient": recipient or f"recipient:{mission_id}"},
                             commit=False)

    def _read(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        """Reads on the shared connection are serialised: cursors interleaved across threads
        can otherwise return wrong rows."""
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    def mission(self, mission_id: str) -> sqlite3.Row | None:
        rows = self._read("SELECT * FROM missions WHERE id=?", (mission_id,))
        return rows[0] if rows else None

    def event_revision(self, event_id: str) -> int | None:
        rows = self._read("SELECT revision FROM events WHERE id=?", (event_id,))
        return rows[0]["revision"] if rows else None

    def amount_locked(self, c, mission_id: str) -> str | None:
        """Reason the amount may no longer change, else None."""
        if c.execute("SELECT 1 FROM decision_history WHERE mission_id=? AND approval_required=1 LIMIT 1",
                     (mission_id,)).fetchone():
            return "an approval hold exists for this mission"
        if c.execute("SELECT 1 FROM payments WHERE mission_id=? LIMIT 1", (mission_id,)).fetchone():
            return "a payment exists for this mission"
        has_ops = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='operations'").fetchone()
        if has_ops and c.execute("SELECT 1 FROM operations WHERE mission_id=? LIMIT 1", (mission_id,)).fetchone():
            return "a payment operation is reserved for this mission"
        return None

    def apply_event(self, event_id: str, mission_id: str, expected_revision: int, patch: dict, *,
                    actor: str = "system", kind: str = "input.event", summary: str | None = None,
                    party: str | None = None, set_amount_minor: int | None = None,
                    audit_extra: dict | None = None, valid_at: str | None = None,
                    extra: Callable[[sqlite3.Connection, int], None] | None = None) -> int:
        """Atomic, idempotent on event_id, optimistic concurrency on revision. Returns the revision.

        `extra(conn, new_revision)` runs inside the same transaction (approval/override rows).
        Raises KeyError, StaleRevisionError, LatchedFactError or AmountLockedError; on any error
        nothing is written."""
        with self.transaction() as (c, audit):
            m = c.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
            if m is None:
                raise KeyError(mission_id)
            dup = c.execute("SELECT revision FROM events WHERE id=?", (event_id,)).fetchone()
            if dup:
                return dup["revision"]
            if m["revision"] != expected_revision:
                raise StaleRevisionError(f"stale revision: expected {expected_revision}, have {m['revision']}")
            patch = redact(patch)  # secrets never reach events.patch, missions.inputs or the audit log
            current = json.loads(m["inputs"])
            for k in LATCHED_KEYS:
                if current.get(k) is True and k in patch and patch[k] is not True:
                    raise LatchedFactError(
                        f"{k} is latched: it can only be cleared by a new policy version or an approver override")
            if set_amount_minor is not None:
                if isinstance(set_amount_minor, bool) or not isinstance(set_amount_minor, int) or set_amount_minor <= 0:
                    raise ValueError("amount must be positive integer minor units")
                if set_amount_minor != m["amount_minor"]:
                    why = self.amount_locked(c, mission_id)
                    if why:
                        raise AmountLockedError(f"amount is immutable: {why}")
                    c.execute("UPDATE missions SET amount_minor=? WHERE id=?", (set_amount_minor, mission_id))
                    patch = {**patch, "amount_minor": set_amount_minor}
            new_inputs = {**current, **patch}
            rev = m["revision"] + 1
            c.execute("INSERT INTO events VALUES(?,?,?,?)", (event_id, mission_id, rev, json.dumps(patch)))
            cur = c.execute("UPDATE missions SET inputs=?, revision=? WHERE id=? AND revision=?",
                            (json.dumps(new_inputs), rev, mission_id, m["revision"]))
            if cur.rowcount != 1:  # lost a race: roll back, caller retries on the new revision
                raise StaleRevisionError("concurrent update")
            if extra is not None:
                extra(c, rev)
            audit.append(actor, kind, summary or f"{kind} {event_id} -> revision {rev}",
                         mission_id=mission_id, party=party,
                         payload={"event_id": event_id, "revision": rev, "patch": patch, **(audit_extra or {})},
                         valid_at=valid_at, commit=False)
            return rev

    def audit_note(self, mission_id: str | None, actor: str, summary: str, payload: dict | None = None,
                   kind: str = "input.rejected", *, stripe: dict | None = None, mode: str | None = None,
                   policy: dict | None = None, party: str | None = None) -> None:
        """Append one audit entry on its own transaction (refusals are as visible as acceptances)."""
        with self.transaction() as (_c, audit):
            audit.append(actor, kind, summary, mission_id=mission_id, payload=payload or {}, stripe=stripe,
                         mode=mode, policy=policy, party=party, commit=False)

    audit_rejection = audit_note

    # ------------------------------------------------------------ decisions
    def record_decision(self, mission_id: str, revision: int, body: dict) -> bool:
        """Store the latest decision for this revision and append to the history when it changed.
        Returns True when this decision differs from the previous one (caller audits it)."""
        key = body.get("decision_key") or hashlib.sha256(
            json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        with self.transaction() as (c, _audit):
            last = c.execute("SELECT key FROM decision_history WHERE mission_id=? ORDER BY id DESC LIMIT 1",
                             (mission_id,)).fetchone()
            c.execute("INSERT OR REPLACE INTO decisions VALUES(?,?,?)", (mission_id, revision, json.dumps(body)))
            if last is not None and last["key"] == key:
                return False
            c.execute("INSERT INTO decision_history(mission_id,revision,key,approval_required,body) VALUES(?,?,?,?,?)",
                      (mission_id, revision, key, 1 if body.get("approval_required") else 0, json.dumps(body)))
            return True

    def latch(self, mission_id: str) -> dict | None:
        rows = self._read("SELECT * FROM deny_latch WHERE mission_id=?", (mission_id,))
        if not rows:
            return None
        r = rows[0]
        return {"policy_hash": r["policy_hash"], "revision": r["revision"],
                "clauses": json.loads(r["clauses"]), "ts": r["ts"]}

    def set_latch(self, mission_id: str, policy_hash: str, revision: int, clauses: list[list[str]]) -> None:
        with self.transaction() as (c, _a):
            c.execute("INSERT OR REPLACE INTO deny_latch VALUES(?,?,?,?,?)",
                      (mission_id, policy_hash, revision, json.dumps(clauses), _now()))

    def approvals(self, mission_id: str) -> list[dict]:
        return [dict(r) for r in self._read("SELECT * FROM approvals WHERE mission_id=? ORDER BY id", (mission_id,))]

    def overrides(self, mission_id: str) -> list[dict]:
        return [dict(r) for r in self._read("SELECT * FROM overrides WHERE mission_id=? ORDER BY id", (mission_id,))]

    # ------------------------------------------------------------ payments
    def pay_once(self, operation_id: str, mission_id: str, amount_minor: int, currency: str,
                 mode: str, provider_ref: str) -> bool:
        """True if this call created the effect; False if it already existed."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO payments VALUES(?,?,?,?,?,?)",
            (operation_id, mission_id, amount_minor, currency, mode, provider_ref),
        )
        self.db.commit()
        return cur.rowcount == 1

    def payments_for(self, mission_id: str) -> list[dict]:
        return [dict(r) for r in self._read("SELECT * FROM payments WHERE mission_id=? ORDER BY rowid", (mission_id,))]
