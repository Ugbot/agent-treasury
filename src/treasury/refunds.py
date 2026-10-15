"""REFUND-1: refund a canceled, pre-pickup order exactly once.

Pure SQLite + the ``gateway.PaymentGateway`` contract. Money is integer minor
units. One semantic refund per order: the operation id is derived from the order,
so duplicate cancel events collapse onto one provider effect. Before any retry the
external state is checked (``gateway.find_refund`` when the gateway offers it).
Success is never declared at dispatch: only a provider status of ``succeeded``
yields ``confirmed``.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

from .currency import DEFAULT_CURRENCY
from .currency_guard import currency_denial
from .gateway import CapabilityUnavailable, GatewayError, LiveModeError, PaymentGateway
from .policy import Clause, Policy, evaluate

REFUND_POLICY = Policy(
    id="refund-agent",
    version=1,
    clauses=(
        Clause("REFUND-1", "Cancel before pickup with no recipient transfers permits one full "
               "original-payment refund, capped by the remaining refundable amount",
               "require_input", {"key": "canceled_before_pickup"}),
        Clause("MONEY-1", "Never exceed the remaining refundable amount", "max_amount", {}),
    ),
)

TERMINAL = {"confirmed", "failed", "held", "escalated", "denied"}
IN_FLIGHT_MAP = {"succeeded": "confirmed", "pending": "pending", "requires_action": "pending",
                 "failed": "failed", "canceled": "failed"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS refund_orders(order_id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS refunds(
  order_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS refund_cancel_events(
  event_id TEXT PRIMARY KEY, order_id TEXT NOT NULL);
"""


class RefundError(Exception):
    pass


class UnknownOrder(RefundError):
    pass


class NoPaymentState(RefundError):
    """No real payment/settlement state is available for this order (never client-supplied)."""


@dataclass
class Order:
    order_id: str
    payment_ref: str                    # original collection (provider PaymentIntent/charge)
    amount_minor: int
    currency: str = DEFAULT_CURRENCY
    status: str = "funded"              # funded | picked_up | delivered | canceled
    custody: str = "clear"              # clear | ambiguous
    transfers: list[dict] = field(default_factory=list)   # executed recipient transfers
    refunded_minor: int = 0             # already refunded elsewhere
    outcome_uncertain: bool = False     # a prior provider outcome is unknown
    fuel_captured_minor: int = 0
    processing_fee_minor: int = 0
    settlement_in_progress: bool = False  # proven settlement with legs not yet confirmed

    def __post_init__(self) -> None:
        for name in ("amount_minor", "refunded_minor", "fuel_captured_minor", "processing_fee_minor"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ValueError(f"{name} must be a non-negative integer (minor units)")
        if self.amount_minor <= 0:
            raise ValueError("amount_minor must be positive")
        self.currency = self.currency.lower()

    @property
    def remaining_refundable_minor(self) -> int:
        return max(self.amount_minor - self.refunded_minor, 0)


def _provider(res: Any) -> dict:
    return {"provider_ref": res.provider_ref, "mode": res.mode, "livemode": res.livemode,
            "status": res.status, "payment_ref": getattr(res, "payment_ref", "")}


class RefundService:
    def __init__(self, gateway: PaymentGateway, db_path: str = ":memory:", audit=None,
                 registry=None, settlements=None, ledger=None):
        self.gateway = gateway
        self.audit = audit
        self.registry = registry        # PolicyRegistry: CURRENCY-1 + recovery policies
        self.settlements = settlements  # proof.SettlementService: funding + transfer state
        self.ledger = ledger            # Ledger: saga payments (saga.refundable_payment)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self._lock = threading.RLock()

    def bind(self, *, registry=None, settlements=None, ledger=None) -> None:
        """Integrator hook: attach the real state sources after construction."""
        if registry is not None:
            self.registry = registry
        if settlements is not None:
            self.settlements = settlements
        if ledger is not None:
            self.ledger = ledger

    @property
    def has_state_source(self) -> bool:
        return self.settlements is not None or self.ledger is not None

    # --- real state (never client supplied) ----------------------------------
    def real_state(self, order_id: str) -> dict | None:
        """Payment + transfer state read from the saga/ledger and the settlement service."""
        pay: dict | None = None
        if self.ledger is not None:
            from .saga import refundable_payment
            rp = refundable_payment(self.ledger, order_id)
            if rp is not None:
                pay = {"payment_ref": rp["provider_ref"] or "", "amount_minor": rp["amount_minor"],
                       "currency": rp["currency"], "refunded_minor": rp["refunded_minor"],
                       "paid": rp["refundable_minor"] > 0 or rp["refunded_minor"] > 0}
        st = None
        if self.settlements is not None:
            f = self.settlements.funding.get(order_id)
            o = self.settlements.orders.get(order_id)
            if pay is None and f and o:
                pay = {"payment_ref": f.get("payment_ref", ""), "amount_minor": f["amount_minor"],
                       "currency": o.currency.lower(), "refunded_minor": 0, "paid": True}
            st = self.settlements.settlements.get(order_id)
        if pay is None or not pay["paid"] or not pay["payment_ref"]:
            return None
        transfers, in_progress = [], False
        if st is not None and st.proven:
            for name, leg in st.legs.items():
                if leg.status == "confirmed":
                    transfers.append({"id": leg.provider_ref, "leg": name,
                                      "amount_minor": leg.amount_minor})
                else:
                    in_progress = True
        return {**pay, "transfers": transfers, "settlement_in_progress": in_progress}

    def order_from_state(self, order_id: str, status: str = "funded", custody: str = "clear",
                         outcome_uncertain: bool = False, fuel_captured_minor: int = 0,
                         processing_fee_minor: int = 0) -> Order:
        """Build and register an order whose money facts come from real payment state."""
        rs = self.real_state(order_id)
        if rs is None:
            raise NoPaymentState(order_id)
        return self.register_order(Order(
            order_id, rs["payment_ref"], rs["amount_minor"], rs["currency"], status, custody,
            rs["transfers"], rs["refunded_minor"], outcome_uncertain, fuel_captured_minor,
            processing_fee_minor, rs["settlement_in_progress"]))

    def _refresh(self, order: Order) -> Order:
        """Overlay real payment/transfer state on a registered order (no-op without sources)."""
        if not self.has_state_source:
            return order
        rs = self.real_state(order.order_id)
        if rs is None:
            raise NoPaymentState(order.order_id)
        order.payment_ref, order.amount_minor, order.currency = (
            rs["payment_ref"], rs["amount_minor"], rs["currency"].lower())
        order.refunded_minor = min(order.amount_minor, max(order.refunded_minor, rs["refunded_minor"]))
        order.transfers = rs["transfers"]
        order.settlement_in_progress = rs["settlement_in_progress"]
        return self.register_order(order)

    def _party(self, order_id: str) -> str:
        o = self.settlements.orders.get(order_id) if self.settlements is not None else None
        return o.recipient_id if o is not None else "customer"

    # --- recovery policy (resolved by id+version from the registry) -------------
    def resolve_recovery_policy(self, ref: dict | None, order: Order, headroom: int) -> dict:
        """Return {"ok": True, id, version, hash, cap_minor} or {"ok": False, "reason": ...}."""
        if not isinstance(ref, dict) or not ref.get("id") or not isinstance(ref.get("version"), int) \
                or isinstance(ref.get("version"), bool):
            return {"ok": False, "reason": "refund_after_transfer_requires_recovery_policy"}
        if self.registry is None:
            return {"ok": False, "reason": "recovery_policy_registry_unavailable"}
        from .policies import NotFoundError, _weakens
        try:
            b = self.registry.get(ref["id"], ref["version"])
        except NotFoundError:
            return {"ok": False, "reason": "recovery_policy_not_published"}
        if not b.verify():
            return {"ok": False, "reason": "recovery_policy_hash_mismatch"}
        for a in self.registry.effective_attachments([]):
            if not a.mandatory:
                continue
            mb = self.registry.get(a.policy_id, a.policy_version)
            shared = {c.id for c in b.clauses} & {c.id for c in mb.clauses}
            if shared and _weakens(tuple(c for c in mb.clauses if c.id in shared),
                                   tuple(c for c in b.clauses if c.id in shared)):
                return {"ok": False, "reason": "recovery_policy_weakens_mandatory_policy"}
            if b.policy_id == mb.policy_id and b.version != self.registry.get(b.policy_id).version:
                return {"ok": False, "reason": "recovery_policy_stale"}
        limits = [c.params["limit_minor"] for c in b.clauses if c.kind == "max_amount"]
        cap = min([headroom, *limits])
        d = evaluate(b.as_policy(), {"currency": order.currency, "refund_after_transfer": True},
                     1, cap)
        if d.verdict != "allow":
            return {"ok": False, "reason": f"recovery_policy_{d.verdict}"}
        return {"ok": True, "id": b.policy_id, "version": b.version, "hash": b.content_hash,
                "cap_minor": cap}

    # --- orders -----------------------------------------------------------
    def register_order(self, order: Order) -> Order:
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO refund_orders VALUES(?,?)",
                            (order.order_id, json.dumps(asdict(order))))
            self.db.commit()
        return order

    def get_order(self, order_id: str) -> Order:
        row = self.db.execute("SELECT body FROM refund_orders WHERE order_id=?", (order_id,)).fetchone()
        if row is None:
            raise UnknownOrder(order_id)
        return Order(**json.loads(row[0]))

    def _audit(self, summary: str, rec: dict, **payload) -> None:
        if self.audit is not None:
            self.audit.append("system", "refund", summary, mission_id=rec["order_id"],
                              party=rec.get("party") or "customer",
                              payload={"operation_id": rec["operation_id"], **payload},
                              policy={"id": rec["decision"].get("policy_id"),
                                      "version": rec["decision"].get("policy_version"),
                                      "hash": rec["decision"].get("policy_hash"),
                                      "verdict": rec["decision"].get("verdict"),
                                      "clauses": [rec["decision"].get("clause")]},
                              mode=getattr(self.gateway, "mode", None))

    @staticmethod
    def operation_id(order_id: str) -> str:
        return f"refund:{order_id}"

    # --- records ----------------------------------------------------------
    def get(self, order_id: str) -> dict | None:
        row = self.db.execute("SELECT body FROM refunds WHERE order_id=?", (order_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _save(self, rec: dict) -> dict:
        self.db.execute("INSERT OR REPLACE INTO refunds VALUES(?,?,?)",
                        (rec["order_id"], rec["operation_id"], json.dumps(rec)))
        self.db.commit()
        return rec

    # --- decision ---------------------------------------------------------
    def decide(self, order: Order, recovery_policy: dict | None = None) -> dict:
        """Deterministic REFUND-1 decision. `recovery_policy` is only a reference
        ({"id", "version"}); it is resolved from the PolicyRegistry, never trusted as given."""
        remaining = order.remaining_refundable_minor
        base = {"clause": "REFUND-1", "policy_id": REFUND_POLICY.id,
                "policy_version": REFUND_POLICY.version, "policy_hash": REFUND_POLICY.hash,
                "remaining_refundable_minor": remaining}

        def out(verdict, reason, amount=0, clause="REFUND-1"):
            return {**base, "clause": clause, "verdict": verdict, "reason": reason,
                    "amount_minor": amount}

        den = currency_denial(order.currency, self.registry)
        if den is not None:
            return {**out("deny", "currency_not_permitted", clause=den["clause"]),
                    "currency": den["currency"], "allowed": den["allowed"],
                    "currency_policy_id": den["policy_id"],
                    "currency_policy_version": den["policy_version"], "detail": den["reason"]}
        if order.refunded_minor > 0 and remaining == 0:
            return out("hold", "already_refunded", clause="MONEY-1")
        if order.outcome_uncertain:
            return out("hold", "uncertain_outcome")
        if order.settlement_in_progress:
            return out("hold", "settlement_in_progress")
        if order.status == "delivered":
            return out("hold", "already_delivered")
        if order.custody != "clear":
            return out("hold", "custody_ambiguous")
        if order.status == "picked_up":
            return out("hold", "after_pickup")
        if order.status != "canceled":
            return out("hold", "not_canceled")
        if remaining == 0:
            return out("hold", "nothing_refundable", clause="MONEY-1")
        if order.transfers:
            transferred = sum(int(t.get("amount_minor", 0)) for t in order.transfers)
            headroom = max(0, remaining - transferred)  # transferred money is not refundable here
            if headroom <= 0:
                return out("hold", "nothing_refundable_after_transfers", clause="MONEY-1")
            rp = self.resolve_recovery_policy(recovery_policy, order, headroom)
            if not rp["ok"]:
                return out("deny", rp["reason"])
            return {**out("allow", "recovery_policy_permits_refund", rp["cap_minor"]),
                    "recovery_policy_id": rp["id"], "recovery_policy_version": rp["version"],
                    "recovery_policy_hash": rp["hash"]}
        return out("allow", "canceled_before_pickup_no_transfers", remaining)

    # --- main entry -------------------------------------------------------
    def request_refund(self, order_id: str, cancel_event_id: str, cancel_evidence: dict | None = None,
                       recovery_policy: dict | None = None) -> dict:
        with self._lock:
            order = self.get_order(order_id)
            try:
                order = self._refresh(order)
            except NoPaymentState:
                return self._no_payment(order_id, cancel_event_id)
            self.db.execute("INSERT OR IGNORE INTO refund_cancel_events VALUES(?,?)",
                            (cancel_event_id, order_id))
            existing = self.get(order_id)
            if existing is not None and existing["state"] not in ("unknown", "reserved", "unavailable"):
                if existing["state"] in ("pending", "submitted"):
                    return self.reconcile(order_id)
                return {**existing, "duplicate": True}
            if existing is None:
                decision = self.decide(order, recovery_policy)
                rec = {
                    "order_id": order_id, "operation_id": self.operation_id(order_id),
                    "party": self._party(order_id), "kind": "refund", "currency": order.currency, "decision": decision,
                    "amount_minor": decision["amount_minor"], "original_payment_ref": order.payment_ref,
                    "cancel_event_ids": [cancel_event_id], "cancel_evidence": cancel_evidence or {},
                    "provider": None, "message": None,
                    "allowance_restored": False, "transfers_reversed": False,
                    "costs": {"fuel_captured_minor": order.fuel_captured_minor,
                              "processing_fee_minor": order.processing_fee_minor},
                    "state": {"allow": "reserved", "hold": "held", "deny": "denied"}[decision["verdict"]],
                }
                if decision["verdict"] == "hold":
                    rec["state"] = "escalated" if decision["reason"] in (
                        "uncertain_outcome", "custody_ambiguous", "already_delivered") else "held"
                self._save(rec)
                self._audit(f"refund decision {decision['verdict']}: {decision['reason']}", rec,
                            reason=decision["reason"], amount_minor=rec["amount_minor"],
                            state=rec["state"])
                if rec["state"] != "reserved":
                    return rec
            else:
                rec = existing
                if cancel_event_id not in rec["cancel_event_ids"]:
                    rec["cancel_event_ids"].append(cancel_event_id)
            return self._dispatch(rec)

    def _no_payment(self, order_id: str, cancel_event_id: str) -> dict:
        """Denied without persisting, so a later real payment can still be refunded."""
        decision = {"clause": "MONEY-1", "policy_id": REFUND_POLICY.id,
                    "policy_version": REFUND_POLICY.version, "policy_hash": REFUND_POLICY.hash,
                    "verdict": "deny", "reason": "no_confirmed_payment", "amount_minor": 0,
                    "remaining_refundable_minor": 0}
        rec = {"order_id": order_id, "operation_id": self.operation_id(order_id),
               "party": self._party(order_id), "kind": "refund", "decision": decision,
               "amount_minor": 0, "state": "denied", "cancel_event_ids": [cancel_event_id],
               "provider": None, "message": "no confirmed payment for this order"}
        self._audit("refund denied: no confirmed payment", rec, reason="no_confirmed_payment",
                    state="denied")
        return rec

    def _dispatch(self, rec: dict) -> dict:
        op = rec["operation_id"]
        # Check external state before (re)submitting.
        found = self._lookup(op)
        if found is not None:
            return self._apply(rec, found)
        try:
            res = self.gateway.refund(op, rec["original_payment_ref"], rec["amount_minor"])
        except CapabilityUnavailable as exc:
            rec.update(state="unavailable", message=f"{exc} Setup: {exc.setup}")
            self._audit("refund provider unavailable", rec, outcome="unavailable")
            return self._save(rec)
        except LiveModeError:
            rec.update(state="failed", message="live-mode object or credential rejected")
            self._save(rec)
            self._audit("refund blocked: livemode", rec, outcome="livemode_rejected")
            raise
        except GatewayError as exc:
            rec.update(state="unknown", message=f"provider outcome unknown: {type(exc).__name__}")
            self._audit("refund provider outcome unknown", rec, outcome="unknown")
            return self._save(rec)
        return self._apply(rec, res)

    def _lookup(self, operation_id: str):
        finder = getattr(self.gateway, "find_refund", None)
        if finder is None:
            return None
        try:
            return finder(operation_id)
        except GatewayError:
            return None

    def _apply(self, rec: dict, res: Any) -> dict:
        if res.livemode:
            rec.update(state="failed", message="livemode=true object rejected")
            self._save(rec)
            raise LiveModeError("livemode=true refund")
        state = IN_FLIGHT_MAP.get(res.status, "submitted")
        rec.update(state=state, provider=_provider(res), message=None)
        if state == "confirmed":
            order = self.get_order(rec["order_id"])
            order.refunded_minor = min(order.amount_minor, order.refunded_minor + rec["amount_minor"])
            self.register_order(order)
        self._save(rec)
        self._audit(f"refund {state} ({rec['amount_minor']})", rec, outcome=state,
                    refund=res.provider_ref, payment_ref=rec["original_payment_ref"],
                    amount_minor=rec["amount_minor"], livemode=res.livemode)
        return rec

    def reconcile(self, order_id: str) -> dict:
        """Refresh a submitted/pending/unknown refund from external state."""
        with self._lock:
            rec = self.get(order_id)
            if rec is None:
                raise UnknownOrder(order_id)
            if rec["state"] in ("confirmed", "failed", "held", "escalated", "denied"):
                return rec
            found = self._lookup(rec["operation_id"])
            if found is not None:
                return self._apply(rec, found)
            if rec["state"] in ("unknown", "reserved", "unavailable"):
                return self._dispatch(rec)   # same operation id: provider-idempotent
            return rec
