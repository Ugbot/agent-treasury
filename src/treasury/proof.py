"""Proof-gated settlement: merchant and driver transfers released only on valid delivery proof.

Gates (all evaluated, all must pass): assigned driver, fresh position, inside destination
radius, one-use recipient receipt nonce (bound to order/recipient/assignment, expiring,
rate limited). Settlement is two independent transfers with stable operation IDs; this is
PARTIAL-capable settlement, never atomic, and never a bank payout. Money is integer minor
units; all times are simulated seconds.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass, field

from .currency_guard import currency_denial, live_default_currency
from .gateway import (
    CapabilityUnavailable,
    GatewayError,
    LiveModeError,
    OutcomeUnknown,
    PaymentGateway,
)
from .geo import Point, position_fresh, within_radius

RADIUS_M = 100.0
MAX_POSITION_AGE_S = 60.0
NONCE_TTL_S = 120.0
RATE_LIMIT_ATTEMPTS = 5
RATE_LIMIT_WINDOW_S = 60.0
AUTO_TRANSFER_LIMIT_MINOR = 10_000
LEGS = ("merchant", "driver")

GATES = ("assigned_driver", "position_fresh", "within_radius", "receipt_nonce")


class ProofError(Exception):
    pass


class PolicyDenied(ProofError):
    """A policy clause (e.g. CURRENCY-1) denied the action; `info` names the clause."""

    def __init__(self, info: dict):
        super().__init__(info["reason"])
        self.info = info


@dataclass(frozen=True)
class Order:
    id: str
    recipient_id: str
    assigned_driver: str
    assignment_version: int
    destination: Point
    merchant_destination: str
    driver_destination: str
    total_minor: int = 10_000
    merchant_minor: int = 7_000
    driver_minor: int = 2_000
    retention_minor: int = 1_000
    currency: str = field(default_factory=live_default_currency)
    policy_version: int = 1

    def __post_init__(self):
        for v in (self.total_minor, self.merchant_minor, self.driver_minor, self.retention_minor):
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ProofError("amounts must be non-negative integer minor units")
        if self.merchant_minor + self.driver_minor + self.retention_minor != self.total_minor:
            raise ProofError("split must sum to total")

    def leg_amount(self, leg: str) -> int:
        return self.merchant_minor if leg == "merchant" else self.driver_minor

    def leg_destination(self, leg: str) -> str:
        return self.merchant_destination if leg == "merchant" else self.driver_destination


def seed_order(order_id: str = "ord_1", **kw) -> Order:
    """GBP100 order: 70 merchant, 20 driver, 10 gross platform retention."""
    base = {"id": order_id, "recipient_id": "rcp_1", "assigned_driver": "drv_1", "assignment_version": 1,
                "destination": Point(51.5, -0.12), "merchant_destination": "acct_merchant_test",
                "driver_destination": "acct_driver_test"}
    base.update(kw)
    return Order(**base)  # type: ignore[arg-type]


def balance_view(order: Order, stripe_fee_minor: int = 0, fuel_cost_minor: int = 0) -> dict:
    """Gross split, with Stripe fees and fuel operating cost shown separately."""
    return {"currency": order.currency, "gross_minor": order.total_minor,
            "merchant_minor": order.merchant_minor, "driver_minor": order.driver_minor,
            "platform_retention_gross_minor": order.retention_minor,
            "stripe_fee_minor": stripe_fee_minor, "fuel_operating_cost_minor": fuel_cost_minor,
            "platform_net_minor": order.retention_minor - stripe_fee_minor - fuel_cost_minor,
            "note": "retention is gross before Stripe fees; fuel cost is a separate operating cost"}


@dataclass(frozen=True)
class ProofResult:
    ok: bool
    gates: dict[str, bool]
    reasons: dict[str, str]
    distance_ok: bool = False
    rate_limited: bool = False

    @property
    def failed(self) -> list[str]:
        return [g for g, v in self.gates.items() if not v]


@dataclass
class _Nonce:
    digest: str
    order_id: str
    recipient_id: str
    assignment_version: int
    expires_at: float
    used: bool = False


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ReceiptNonces:
    """One-use recipient nonces. Only digests are stored."""

    def __init__(self, ttl_s: float = NONCE_TTL_S, max_attempts: int = RATE_LIMIT_ATTEMPTS,
                 window_s: float = RATE_LIMIT_WINDOW_S):
        self.ttl_s, self.max_attempts, self.window_s = ttl_s, max_attempts, window_s
        self._nonces: dict[str, _Nonce] = {}
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def issue(self, order: Order, now: float) -> str:
        token = secrets.token_urlsafe(16)
        with self._lock:
            self._nonces[_digest(token)] = _Nonce(
                _digest(token), order.id, order.recipient_id, order.assignment_version,
                now + self.ttl_s)
        return token

    def rate_limited(self, order_id: str, now: float) -> bool:
        """Record an attempt; True when the window budget is exhausted."""
        with self._lock:
            hist = [t for t in self._attempts.get(order_id, []) if now - t < self.window_s]
            if len(hist) >= self.max_attempts:
                self._attempts[order_id] = hist
                return True
            hist.append(now)
            self._attempts[order_id] = hist
            return False

    def check(self, order: Order, recipient_id: str, token: str, now: float) -> tuple[bool, str]:
        with self._lock:
            n = self._nonces.get(_digest(token or ""))
            if n is None:
                return False, "unknown receipt nonce"
            if n.used:
                return False, "receipt nonce already used"
            if now > n.expires_at:
                return False, "receipt nonce expired"
            if not (hmac.compare_digest(n.order_id, order.id)
                    and hmac.compare_digest(n.recipient_id, recipient_id)
                    and n.recipient_id == order.recipient_id):
                return False, "receipt nonce not bound to this order/recipient"
            if n.assignment_version != order.assignment_version:
                return False, "assignment changed since nonce issued"
            return True, ""

    def consume(self, token: str) -> bool:
        with self._lock:
            n = self._nonces.get(_digest(token))
            if n is None or n.used:
                return False
            n.used = True
            return True


def verify_proof(order: Order, nonces: ReceiptNonces, *, driver_id: str,
                 position: Point, position_ts: float, recipient_id: str, nonce: str,
                 assignment_version: int | None = None, now: float,
                 consume: bool = True) -> ProofResult:
    """Evaluate every gate (no short-circuit). Consume the nonce only if all pass."""
    limited = nonces.rate_limited(order.id, now)
    gates: dict[str, bool] = {}
    reasons: dict[str, str] = {}
    av = order.assignment_version if assignment_version is None else assignment_version
    gates["assigned_driver"] = driver_id == order.assigned_driver and av == order.assignment_version
    if not gates["assigned_driver"]:
        reasons["assigned_driver"] = "driver or assignment version does not match order"
    gates["position_fresh"] = position_fresh(position_ts, now, MAX_POSITION_AGE_S)
    if not gates["position_fresh"]:
        reasons["position_fresh"] = f"position older than {MAX_POSITION_AGE_S:.0f}s or from future"
    gates["within_radius"] = within_radius(position, order.destination, RADIUS_M)
    if not gates["within_radius"]:
        reasons["within_radius"] = f"outside {RADIUS_M:.0f}m destination radius"
    if limited:
        gates["receipt_nonce"], reasons["receipt_nonce"] = False, "rate limited"
    else:
        ok, why = nonces.check(order, recipient_id, nonce, now)
        gates["receipt_nonce"] = ok
        if not ok:
            reasons["receipt_nonce"] = why
    ok_all = all(gates.values())
    if ok_all and consume and not nonces.consume(nonce):  # lost a race: replay
        gates["receipt_nonce"], reasons["receipt_nonce"], ok_all = False, "receipt nonce already used", False
    return ProofResult(ok_all, gates, reasons, gates["within_radius"], limited)


# ---------------------------------------------------------------- settlement

def approval_digest(operation_id: str, amount_minor: int, destination: str,
                    policy_version: int) -> str:
    return hashlib.sha256(
        f"{operation_id}|{amount_minor}|{destination}|{policy_version}".encode()).hexdigest()


@dataclass
class Leg:
    leg: str
    operation_id: str
    amount_minor: int
    destination: str
    status: str = "pending"  # pending | awaiting_approval | confirmed | failed
    provider_ref: str = ""
    mode: str = ""
    livemode: bool = False
    error: str = ""
    attempts: int = 0
    source_transaction: str = ""  # ch_... the customer charge funding this transfer
    transfer_group: str = ""


@dataclass
class Settlement:
    order_id: str
    proven: bool = False
    evidence: dict = field(default_factory=dict)
    legs: dict[str, Leg] = field(default_factory=dict)
    funding: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        if not self.proven:
            return "held"
        st = [leg.status for leg in self.legs.values()]
        if all(s == "confirmed" for s in st):
            return "released"
        if any(s == "confirmed" for s in st):
            return "partial"
        if any(s == "awaiting_approval" for s in st):
            return "awaiting_approval"
        return "pending_retry"

    def to_dict(self) -> dict:
        return {"order_id": self.order_id, "status": self.status, "proven": self.proven,
                "atomic": False, "bank_payout": False, "evidence": self.evidence,
                "funding": dict(self.funding),
                "legs": {k: vars(v).copy() for k, v in self.legs.items()}}


class SettlementService:
    def __init__(self, gateway: PaymentGateway, nonces: ReceiptNonces | None = None,
                 auto_limit_minor: int = AUTO_TRANSFER_LIMIT_MINOR, audit=None, registry=None):
        self.gateway = gateway
        self.audit = audit
        self.registry = registry  # PolicyRegistry: live CURRENCY-1; None -> live default policy
        self.funding: dict[str, dict] = {}  # order_id -> customer charge on the platform
        self.nonces = nonces or ReceiptNonces()
        self.auto_limit_minor = auto_limit_minor
        self.orders: dict[str, Order] = {}
        self.settlements: dict[str, Settlement] = {}
        self._approvals: dict[str, str] = {}  # operation_id -> digest
        self._lock = threading.RLock()

    def add_order(self, order: Order) -> Order:
        self.orders[order.id] = order
        return order

    def _order(self, order_id: str) -> Order:
        try:
            return self.orders[order_id]
        except KeyError:
            raise ProofError(f"unknown order {order_id}") from None

    @staticmethod
    def operation_id(order: Order, leg: str) -> str:
        return f"settle:{order.id}:{leg}"  # stable; independent per leg

    @staticmethod
    def transfer_group(order: Order) -> str:
        return f"order_{order.id}"

    def _audit(self, kind: str, summary: str, order_id: str, party: str | None = None,
               **payload) -> None:
        if self.audit is not None:
            self.audit.append("system", kind, summary, mission_id=order_id, party=party,
                              payload=payload, mode=getattr(self.gateway, "mode", None))

    def fund_order(self, order_id: str, payment_method: str | None = None) -> dict:
        """Charge the customer once on the platform (separate charges and transfers). The
        charge id becomes `source_transaction` for both legs. Idempotent per order."""
        with self._lock:
            o = self._order(order_id)
            if order_id in self.funding:
                return dict(self.funding[order_id])
            den = currency_denial(o.currency, self.registry)
            if den is not None:
                self._audit("delivery.fund", f"order {o.id} funding denied by {den['clause']}",
                            o.id, party=o.recipient_id, outcome="denied", **den)
                raise PolicyDenied(den)
            kw = {"payment_method": payment_method} if payment_method else {}
            pay = self.gateway.create_payment(f"fund:{o.id}", o.total_minor, o.currency,
                                              transfer_group=self.transfer_group(o), **kw)
            if pay.livemode:
                raise LiveModeError("livemode payment rejected")
            if pay.status != "succeeded":
                raise GatewayError(f"order funding payment is {pay.status}")
            self.funding[order_id] = {"payment_ref": pay.provider_ref,
                                      "charge_ref": getattr(pay, "charge_ref", ""),
                                      "transfer_group": self.transfer_group(o),
                                      "amount_minor": pay.amount_minor, "mode": pay.mode}
            self._audit("delivery.fund", f"order {o.id} funded on platform ({pay.amount_minor})",
                        o.id, payment_ref=pay.provider_ref, charge_ref=self.funding[order_id]["charge_ref"],
                        transfer_group=self.transfer_group(o))
            return dict(self.funding[order_id])

    def issue_nonce(self, order_id: str, now: float) -> str:
        return self.nonces.issue(self._order(order_id), now)

    def approve(self, order_id: str, leg: str, approver: str) -> dict:
        """Approval is bound to the operation, amount, recipient and policy version now."""
        if leg not in LEGS:
            raise ProofError("unknown leg")
        o = self._order(order_id)
        op = self.operation_id(o, leg)
        self._approvals[op] = approval_digest(op, o.leg_amount(leg), o.leg_destination(leg),
                                              o.policy_version)
        return {"operation_id": op, "approver": approver, "bound": True}

    def _approved(self, o: Order, leg: str) -> bool:
        op = self.operation_id(o, leg)
        return self._approvals.get(op) == approval_digest(
            op, o.leg_amount(leg), o.leg_destination(leg), o.policy_version)

    def release(self, order_id: str, *, driver_id: str, position: Point, position_ts: float,
                recipient_id: str, nonce: str, now: float,
                assignment_version: int | None = None) -> dict:
        with self._lock:
            o = self._order(order_id)
            st = self.settlements.get(order_id)
            if st and st.proven:  # replay never authorises or pays again
                return {**st.to_dict(), "replay": True, "proof": None}
            if order_id not in self.funding:  # nothing to release: no transfer, nonce untouched
                self._audit("delivery.leg", f"release for {order_id} held: order not funded",
                            order_id, party=driver_id, outcome="held", reason="order_not_funded")
                return {"order_id": order_id, "status": "held", "proven": False,
                        "reason": "order_not_funded", "proof": None, "legs": {},
                        "atomic": False, "bank_payout": False}
            den = currency_denial(o.currency, self.registry)
            if den is not None:
                self._audit("delivery.leg", f"release for {order_id} denied by {den['clause']}",
                            order_id, party=driver_id, outcome="denied", **den)
                return {"order_id": order_id, "status": "denied", "proven": False, "proof": None,
                        "reason": den["reason"], "clause": den["clause"], "policy": den,
                        "legs": {}, "atomic": False, "bank_payout": False}
            proof = verify_proof(o, self.nonces, driver_id=driver_id, position=position,
                                 position_ts=position_ts, recipient_id=recipient_id, nonce=nonce,
                                 assignment_version=assignment_version, now=now)
            self._audit("delivery.proof", f"proof for {order_id}: {'ok' if proof.ok else 'failed'}",
                        order_id, party=driver_id, gates=proof.gates, reasons=proof.reasons,
                        ok=proof.ok, rate_limited=proof.rate_limited)
            if not proof.ok:
                verdict = "held" if proof.failed == ["position_fresh"] or proof.rate_limited else "denied"
                return {"order_id": order_id, "status": verdict, "proven": False,
                        "proof": {"gates": proof.gates, "reasons": proof.reasons},
                        "legs": {}, "atomic": False, "bank_payout": False}
            st = Settlement(order_id, proven=True, evidence={
                "gates": proof.gates, "driver_id": driver_id, "recipient_id": recipient_id,
                "assignment_version": o.assignment_version, "policy_version": o.policy_version,
                "position_ts": position_ts, "proof_time": now,
                "nonce_digest": _digest(nonce)[:16]})
            for leg in LEGS:
                st.legs[leg] = Leg(leg, self.operation_id(o, leg), o.leg_amount(leg),
                                   o.leg_destination(leg))
            st.funding = dict(self.funding.get(order_id, {}))
            self.settlements[order_id] = st
            self._run_legs(o, st)
            return {**st.to_dict(), "proof": {"gates": proof.gates, "reasons": {}}}

    def retry(self, order_id: str) -> dict:
        """Retry only unfinished legs; confirmed legs are never touched."""
        with self._lock:
            o = self._order(order_id)
            st = self.settlements.get(order_id)
            if st is None or not st.proven:
                raise ProofError("no proven settlement to retry")
            self._run_legs(o, st)
            return st.to_dict()

    def get(self, order_id: str) -> dict:
        st = self.settlements.get(order_id)
        return st.to_dict() if st else {"order_id": order_id, "status": "held", "proven": False,
                                         "legs": {}, "atomic": False, "bank_payout": False}

    def _clause_block(self, o: Order, leg: Leg) -> list[str]:
        """Freeze / counterparty / velocity clauses of the mandatory org policies, per leg."""
        from .decision import clause_reasons, server_facts
        from .routes_policies import get_registry
        reg = self.registry or get_registry()
        return clause_reasons(reg, server_facts(self.audit, o.id, leg.destination, None))

    def _run_legs(self, o: Order, st: Settlement) -> None:
        den = currency_denial(o.currency, self.registry)
        for name, leg in st.legs.items():
            if leg.status == "confirmed":
                continue
            if o.id not in self.funding:
                leg.status, leg.error = "failed", "order_not_funded"
                self._audit("delivery.leg", f"{name} leg not paid: order not funded", o.id,
                            party=name, operation_id=leg.operation_id, outcome="held")
                continue
            if den is not None:
                leg.status, leg.error = "failed", den["reason"]
                self._audit("delivery.leg", f"{name} leg denied by {den['clause']}", o.id,
                            party=name, operation_id=leg.operation_id, outcome="denied", **den)
                continue
            blocked = self._clause_block(o, leg)
            if blocked:
                leg.status, leg.error = "failed", "; ".join(blocked)
                self._audit("delivery.leg", f"{name} leg blocked by {blocked[0]}", o.id, party=name,
                            operation_id=leg.operation_id, outcome="held", reasons=blocked,
                            clause=blocked[0].split(":", 1)[-1])
                continue
            if leg.amount_minor > self.auto_limit_minor and not self._approved(o, name):
                leg.status = "awaiting_approval"
                self._audit("delivery.leg", f"{name} leg awaiting approval", o.id,
                            operation_id=leg.operation_id, amount_minor=leg.amount_minor)
                continue
            leg.attempts += 1
            fund = st.funding or self.funding.get(o.id, {})
            src = fund.get("charge_ref") or None
            group = self.transfer_group(o)
            try:
                r = self.gateway.transfer(leg.operation_id, leg.amount_minor, o.currency,
                                          leg.destination, source_transaction=src,
                                          transfer_group=group)
            except LiveModeError:
                self._audit("delivery.leg", f"{name} leg blocked: livemode", o.id,
                            operation_id=leg.operation_id, outcome="livemode_rejected")
                raise
            except CapabilityUnavailable as e:
                leg.status, leg.error = "failed", f"unavailable: {e.reason}; setup: {e.setup}"
                self._audit("delivery.leg", f"{name} leg unavailable", o.id,
                            operation_id=leg.operation_id, outcome="unavailable", error=e.reason[:200])
                continue
            except GatewayError as e:
                leg.status, leg.error = "failed", str(e)
                self._audit("delivery.leg", f"{name} leg failed", o.id,
                            operation_id=leg.operation_id,
                            outcome="unknown" if isinstance(e, OutcomeUnknown) else "failed",
                            error=str(e)[:200])
                continue
            if r.livemode:
                raise LiveModeError("livemode transfer rejected")
            leg.status, leg.provider_ref, leg.mode, leg.livemode, leg.error = (
                "confirmed", r.provider_ref, r.mode, r.livemode, "")
            leg.source_transaction, leg.transfer_group = src or "", group
            self._audit("delivery.leg", f"{name} leg transferred ({leg.amount_minor})", o.id,
                        party=name, operation_id=leg.operation_id, outcome="confirmed",
                        transfer=r.provider_ref, destination=leg.destination,
                        source_transaction=leg.source_transaction, transfer_group=group,
                        amount_minor=leg.amount_minor, livemode=r.livemode)
