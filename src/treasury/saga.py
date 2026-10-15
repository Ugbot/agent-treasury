"""Operation store and saga logic: reserve-before-I/O, reconcile-before-retry, compensation.

Pure SQLite + a gateway Protocol; no DBOS import so it is testable on its own.
Lifecycle: reserved -> submitted -> confirmed, with unknown / failed /
compensating / compensated / compensation_required branches.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Protocol

from .ledger import Ledger

Mode = Literal["mock", "stripe_test"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS operations(
  operation_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, kind TEXT NOT NULL,
  amount_minor INTEGER NOT NULL CHECK(amount_minor > 0), currency TEXT NOT NULL,
  request_hash TEXT NOT NULL, state TEXT NOT NULL, mode TEXT NOT NULL,
  provider_ref TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  parent_id TEXT, evidence TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS reservations(
  operation_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL,
  amount_minor INTEGER NOT NULL, currency TEXT NOT NULL, released INTEGER NOT NULL DEFAULT 0);
"""


class ProviderError(Exception):
    """Provider call failed; outcome may be unknown."""


class GatewayUnavailable(Exception):
    """Gateway cannot run (e.g. missing Stripe test credentials). Message says how to fix."""


@dataclass(frozen=True)
class ProviderResult:
    provider_ref: str
    mode: Mode


class PaymentGateway(Protocol):
    mode: Mode

    def pay(self, operation_id: str, amount_minor: int, currency: str, idempotency_key: str) -> ProviderResult: ...
    def reconcile(self, operation_id: str) -> ProviderResult | None:
        """Return the provider object for this semantic operation if it exists."""
    def refund(self, operation_id: str, provider_ref: str, idempotency_key: str) -> ProviderResult: ...


class MockGateway:
    """In-memory mock provider. Idempotent per key; refs are prefixed mock_."""

    mode: Mode = "mock"

    def __init__(self) -> None:
        self.objects: dict[str, ProviderResult] = {}
        self.calls: list[tuple[str, str]] = []

    def pay(self, operation_id, amount_minor, currency, idempotency_key):
        self.calls.append(("pay", operation_id))
        return self.objects.setdefault(idempotency_key, ProviderResult(f"mock_{operation_id}", "mock"))

    def reconcile(self, operation_id):
        return self.objects.get(operation_id)

    def refund(self, operation_id, provider_ref, idempotency_key):
        self.calls.append(("refund", operation_id))
        return self.objects.setdefault(idempotency_key, ProviderResult(f"mock_refund_{operation_id}", "mock"))


def request_hash(mission_id: str, kind: str, amount_minor: int, currency: str) -> str:
    body = json.dumps([mission_id, kind, amount_minor, currency.upper()])
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def payment_operation_id(mission_id: str) -> str:
    return f"pay:{mission_id}"


def compensation_id(operation_id: str) -> str:
    return f"comp:{operation_id}"


class SagaStore:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.db = ledger.db
        self.db.executescript(SCHEMA)

    # ---- reads
    def get(self, operation_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["evidence"] = json.loads(d["evidence"])
        return d

    def unresolved(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT operation_id FROM operations WHERE state IN ('compensation_required','unknown')"
        ).fetchall()
        return [self.get(r["operation_id"]) for r in rows]

    # ---- writes
    def _set(self, operation_id: str, state: str, provider_ref: str | None = None, note: dict | None = None) -> None:
        op = self.get(operation_id)
        ev = op["evidence"] + ([note] if note else [])
        self.db.execute(
            "UPDATE operations SET state=?, provider_ref=COALESCE(?, provider_ref), evidence=? WHERE operation_id=?",
            (state, provider_ref, json.dumps(ev), operation_id),
        )
        self.db.commit()

    def reserve(self, operation_id: str, mission_id: str, kind: str, amount_minor: int, currency: str,
                mode: str, parent_id: str | None = None) -> dict:
        """Atomically record the intended operation and reserve authority. Idempotent;
        raises ValueError if the same identity is reused with changed parameters."""
        if not isinstance(amount_minor, int) or amount_minor <= 0:
            raise ValueError("amount must be positive integer minor units")
        currency = currency.upper()
        h = request_hash(mission_id, kind, amount_minor, currency)
        existing = self.get(operation_id)
        if existing:
            if existing["request_hash"] != h:
                raise ValueError(f"operation {operation_id} reused with changed parameters")
            return existing
        with self.db:  # one transaction
            self.db.execute(
                "INSERT INTO operations(operation_id,mission_id,kind,amount_minor,currency,request_hash,state,mode,parent_id)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (operation_id, mission_id, kind, amount_minor, currency, h, "reserved", mode, parent_id),
            )
            self.db.execute(
                "INSERT INTO reservations(operation_id,mission_id,amount_minor,currency) VALUES(?,?,?,?)",
                (operation_id, mission_id, amount_minor, currency),
            )
        return self.get(operation_id)

    def reserved_total(self, mission_id: str, currency: str) -> int:
        r = self.db.execute(
            "SELECT COALESCE(SUM(amount_minor),0) s FROM reservations WHERE mission_id=? AND currency=? AND released=0",
            (mission_id, currency.upper()),
        ).fetchone()
        return r["s"]

    def release(self, operation_id: str) -> None:
        self.db.execute("UPDATE reservations SET released=1 WHERE operation_id=?", (operation_id,))
        self.db.commit()

    def _confirm(self, op: dict, res: ProviderResult) -> dict:
        self._set(op["operation_id"], "confirmed", res.provider_ref, {"event": "confirmed", "ref": res.provider_ref})
        if op["kind"] == "payment":
            self.ledger.pay_once(op["operation_id"], op["mission_id"], op["amount_minor"], op["currency"],
                                 res.mode, res.provider_ref)
        return self.get(op["operation_id"])

    # ---- execution
    def execute(self, operation_id: str, gateway: PaymentGateway) -> dict:
        """Run the operation. Reconcile before any retry; never create a second provider object."""
        op = self.get(operation_id)
        if op is None:
            raise KeyError(operation_id)
        if op["state"] in ("confirmed", "compensating", "compensated", "compensation_required"):
            return op
        if op["mode"] != gateway.mode:
            raise ValueError(f"mode mismatch: operation {op['mode']} vs gateway {gateway.mode}")
        if op["state"] in ("submitted", "unknown"):
            found = gateway.reconcile(operation_id)
            if found is not None:
                return self._confirm(op, found)
            self._set(operation_id, "submitted", note={"event": "reconciled_absent_retrying"})
        self.db.execute("UPDATE operations SET attempts=attempts+1 WHERE operation_id=?", (operation_id,))
        self._set(operation_id, "submitted", note={"event": "submitted"})
        try:
            res = gateway.pay(operation_id, op["amount_minor"], op["currency"], operation_id)
        except GatewayUnavailable as exc:
            self._set(operation_id, "failed", note={"event": "unavailable", "message": str(exc)})
            self.release(operation_id)
            raise
        except Exception as exc:
            self._set(operation_id, "unknown", note={"event": "error", "error": type(exc).__name__})
            raise
        return self._confirm(op, res)

    def compensate(self, operation_id: str, gateway: PaymentGateway, *, policy_permits: bool,
                   max_attempts: int = 3) -> dict:
        """Refund a confirmed operation under its own stable identity. Only if provider state is
        confirmed and policy permits; exhausted retries stay compensation_required."""
        op = self.get(operation_id)
        if op is None:
            raise KeyError(operation_id)
        cid = compensation_id(operation_id)
        if not policy_permits:
            self._set(operation_id, op["state"], note={"event": "compensation_denied_by_policy"})
            return self.get(operation_id)
        if op["state"] == "reserved":  # nothing sent: just release authority
            self._set(operation_id, "compensated", note={"event": "reservation_released"})
            self.release(operation_id)
            return self.get(operation_id)
        if op["state"] == "compensated":
            return op
        if op["state"] in ("submitted", "unknown"):
            found = gateway.reconcile(operation_id)
            if found is None:
                self._set(operation_id, "compensated", note={"event": "never_created_released"})
                self.release(operation_id)
                return self.get(operation_id)
            op = self._confirm(op, found)
        if op["state"] == "failed":
            return op
        comp = self.reserve(cid, op["mission_id"], "refund", op["amount_minor"], op["currency"], op["mode"],
                            parent_id=operation_id)
        if comp["state"] == "confirmed":
            self._set(operation_id, "compensated", note={"event": "refunded", "ref": comp["provider_ref"]})
            self.release(operation_id)
            return self.get(operation_id)
        self._set(operation_id, "compensating", note={"event": "compensating"})
        while self.get(cid)["attempts"] < max_attempts:
            self.db.execute("UPDATE operations SET attempts=attempts+1 WHERE operation_id=?", (cid,))
            self.db.commit()
            try:
                prior = gateway.reconcile(cid)
                res = prior or gateway.refund(cid, op["provider_ref"], cid)
            except Exception as exc:  # noqa: BLE001
                self._set(cid, "unknown", note={"event": "error", "error": type(exc).__name__})
                continue
            self._set(cid, "confirmed", res.provider_ref, {"event": "confirmed"})
            self._set(operation_id, "compensated", note={"event": "refunded", "ref": res.provider_ref})
            self.release(operation_id)
            return self.get(operation_id)
        self._set(operation_id, "compensation_required",
                  note={"event": "compensation_exhausted", "attempts": max_attempts, "escalate": True})
        return self.get(operation_id)


def refundable_payment(ledger: Ledger, mission_id: str) -> dict | None:
    """Real payment state for a mission, read from the saga/ledger (never from a client order).

    Returns None when nothing was paid. Refund code must use `refundable_minor` and
    `provider_ref` from here: a payment that is not confirmed, was already compensated, or was
    partly refunded cannot be refunded beyond what remains."""
    store = SagaStore(ledger)
    row = store.db.execute(
        "SELECT * FROM operations WHERE mission_id=? AND kind='payment' ORDER BY rowid LIMIT 1",
        (mission_id,)).fetchone()
    if row is not None:
        op = dict(row)
        refunded = store.db.execute(
            "SELECT COALESCE(SUM(amount_minor),0) s FROM operations "
            "WHERE parent_id=? AND kind='refund' AND state='confirmed'", (op["operation_id"],)).fetchone()["s"]
        if op["state"] == "compensated":
            refunded = max(refunded, op["amount_minor"])
        paid = op["state"] in ("confirmed", "compensating", "compensation_required", "compensated")
        return {"source": "saga", "operation_id": op["operation_id"], "state": op["state"],
                "provider_ref": op["provider_ref"], "amount_minor": op["amount_minor"],
                "currency": op["currency"].lower(), "mode": op["mode"], "refunded_minor": refunded,
                "refundable_minor": max(0, op["amount_minor"] - refunded) if paid else 0}
    pay = ledger.payments_for(mission_id)
    if not pay:
        return None
    p = pay[0]
    return {"source": "ledger", "operation_id": p["operation_id"], "state": "confirmed",
            "provider_ref": p["provider_ref"], "amount_minor": p["amount_minor"],
            "currency": p["currency"].lower(), "mode": p["mode"], "refunded_minor": 0,
            "refundable_minor": p["amount_minor"]}
