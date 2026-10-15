"""SQLite ledger: events, missions and one-effect-per-operation payments."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS missions(
  id TEXT PRIMARY KEY, amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
  currency TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
  inputs TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'open');
CREATE TABLE IF NOT EXISTS events(
  id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(
  mission_id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL,
  PRIMARY KEY(mission_id, revision));
CREATE TABLE IF NOT EXISTS payments(
  operation_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, amount_minor INTEGER NOT NULL,
  currency TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('mock','stripe_test')),
  provider_ref TEXT NOT NULL);
"""


class Ledger:
    def __init__(self, path: str | Path = "treasury.sqlite"):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def create_mission(self, mission_id: str, amount_minor: int, currency: str) -> None:
        if amount_minor <= 0 or not isinstance(amount_minor, int):
            raise ValueError("amount must be positive integer minor units")
        self.db.execute(
            "INSERT OR IGNORE INTO missions(id, amount_minor, currency) VALUES(?,?,?)",
            (mission_id, amount_minor, currency.upper()),
        )
        self.db.commit()

    def mission(self, mission_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()

    def apply_event(self, event_id: str, mission_id: str, expected_revision: int, patch: dict) -> int:
        """Idempotent on event_id; optimistic concurrency on revision. Returns revision."""
        m = self.mission(mission_id)
        if m is None:
            raise KeyError(mission_id)
        if self.db.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
            return m["revision"]
        if m["revision"] != expected_revision:
            raise ValueError(f"stale revision: expected {expected_revision}, have {m['revision']}")
        inputs = {**json.loads(m["inputs"]), **patch}
        rev = m["revision"] + 1
        self.db.execute("INSERT INTO events VALUES(?,?,?,?)", (event_id, mission_id, rev, json.dumps(patch)))
        self.db.execute("UPDATE missions SET inputs=?, revision=? WHERE id=?", (json.dumps(inputs), rev, mission_id))
        self.db.commit()
        return rev

    def record_decision(self, mission_id: str, revision: int, body: dict) -> None:
        self.db.execute("INSERT OR IGNORE INTO decisions VALUES(?,?,?)", (mission_id, revision, json.dumps(body)))
        self.db.commit()

    def pay_once(self, operation_id: str, mission_id: str, amount_minor: int, currency: str,
                 mode: str, provider_ref: str) -> bool:
        """True if this call created the effect; False if it already existed."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO payments VALUES(?,?,?,?,?,?)",
            (operation_id, mission_id, amount_minor, currency, mode, provider_ref),
        )
        self.db.commit()
        return cur.rowcount == 1
