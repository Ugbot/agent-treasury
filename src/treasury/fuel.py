"""Trip fuel allowance enforced through an Issuing card (ATREAS-9).

The authorization decision path reads only prepared local state held in this
object (job state, route, coverage flag, policy snapshot, stop registry). It
never calls an LLM, Cognee or the network. Money is integer minor units.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .currency_guard import currency_denial, live_default_currency
from .gateway import (
    CapabilityUnavailable,
    GatewayError,
    LiveModeError,
    OutcomeUnknown,
    PaymentGateway,
)
from .geo import Point, distance_to_route_m, position_fresh
from .stripe_gateway import check_livemode

LABELS = {
    "merchant": "Simulated test merchant: no real fuel is sold; Stripe test mode only.",
    "category": "Merchant-category controls restrict the merchant type, not the basket contents.",
    "funding": "Issuing test balance is funded separately from customer payments.",
    "creation": "Issuing a card creates authority only; it is not spending until a capture.",
}

WEBHOOK_TOLERANCE_S = 300
RELEASED_STATUSES = frozenset({"reversed", "closed", "expired"})


class SignatureError(Exception):
    pass


@dataclass(frozen=True)
class FuelPolicy:
    version: int = 1
    cap_minor: int = 800
    currency: str = field(default_factory=live_default_currency)
    categories: frozenset[str] = frozenset({"5541", "5542"})
    countries: frozenset[str] = frozenset({"GB"})
    max_route_distance_m: float = 100.0
    max_position_age_s: float = 60.0
    budget_minor: int = 5000  # aggregate across all entitlements


@dataclass
class JobState:
    job_id: str
    status: str = "active"  # active | completed | cancelled | unassigned
    driver_id: str = ""
    assignment_version: int = 0
    coverage_valid: bool = True
    route: tuple[Point, ...] = ()
    position: Point | None = None
    position_ts: float | None = None


@dataclass
class Entitlement:
    entitlement_id: str
    job_id: str
    driver_id: str
    assignment_version: int
    card_ref: str
    policy_version: int
    cap_minor: int
    state: str = "active"  # active | revoked
    revoke_reason: str = ""
    reserved_minor: int = 0
    captured_minor: int = 0
    spending: bool = False
    labels: dict = field(default_factory=lambda: dict(LABELS))

    @property
    def remaining_minor(self) -> int:
        if self.state != "active":
            return 0
        return max(0, self.cap_minor - self.reserved_minor - self.captured_minor)


@dataclass
class AuthRecord:
    authorization_id: str
    entitlement_id: str
    amount_minor: int
    approved: bool
    reasons: tuple[str, ...]
    provider_ref: str = ""
    state: str = "declined"  # declined | reserved | captured | released
    captured_minor: int = 0
    issuing_transaction: str = ""
    release_unresolved: bool = False   # provider has not confirmed the release (in memory)
    release_status: str = ""


SCHEMA = """
CREATE TABLE IF NOT EXISTS fuel_entitlements(
  entitlement_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, driver_id TEXT NOT NULL,
  assignment_version INTEGER NOT NULL, card_ref TEXT NOT NULL, policy_version INTEGER NOT NULL,
  cap_minor INTEGER NOT NULL, state TEXT NOT NULL, revoke_reason TEXT NOT NULL DEFAULT '',
  reserved_minor INTEGER NOT NULL DEFAULT 0, captured_minor INTEGER NOT NULL DEFAULT 0,
  spending INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS fuel_auths(
  authorization_id TEXT PRIMARY KEY, entitlement_id TEXT NOT NULL, amount_minor INTEGER NOT NULL,
  approved INTEGER NOT NULL, reasons TEXT NOT NULL, provider_ref TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL, captured_minor INTEGER NOT NULL DEFAULT 0,
  issuing_transaction TEXT NOT NULL DEFAULT '');
"""


class FuelService:
    """Counters (reserved/captured per entitlement) are written through to SQLite when a `db`
    is given, so a restart cannot reset a cap. Job state is prepared state and is NOT restored:
    after a restart every authorization declines (job_not_active) until jobs are re-prepared."""

    def __init__(self, gateway: PaymentGateway, policy: FuelPolicy | None = None,
                 stops: dict[str, Point] | None = None,
                 clock: Callable[[], float] = time.time,
                 issuing_balance_minor: int | None = None,
                 db=None, audit=None, registry=None):
        self.gateway = gateway
        self.registry = registry  # PolicyRegistry for live CURRENCY-1 (None -> default policy)
        self.db = db
        self.audit = audit
        self.policy = policy or FuelPolicy()
        self.stops = dict(stops or {})
        self.clock = clock
        self.issuing_balance_minor = issuing_balance_minor
        self.jobs: dict[str, JobState] = {}
        self.entitlements: dict[str, Entitlement] = {}
        self.auths: dict[str, AuthRecord] = {}
        self._lock = threading.RLock()
        self._seq = 0
        if db is not None:
            self._load()

    # ---- persistence + audit -------------------------------------------------
    def _load(self) -> None:
        self.db.executescript(SCHEMA)
        for r in self.db.execute("SELECT * FROM fuel_entitlements").fetchall():
            self.entitlements[r[0]] = Entitlement(
                r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], bool(r[11]))
        for r in self.db.execute("SELECT * FROM fuel_auths").fetchall():
            self.auths[r[0]] = AuthRecord(r[0], r[1], r[2], bool(r[3]), tuple(json.loads(r[4])),
                                          r[5], r[6], r[7], r[8])
            tail = r[0].rsplit("_", 1)[-1]
            if tail.isdigit():
                self._seq = max(self._seq, int(tail))

    def _save_e(self, e: Entitlement) -> None:
        if self.db is None:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO fuel_entitlements VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (e.entitlement_id, e.job_id, e.driver_id, e.assignment_version, e.card_ref,
             e.policy_version, e.cap_minor, e.state, e.revoke_reason, e.reserved_minor,
             e.captured_minor, int(e.spending)))
        self.db.commit()

    def _save_a(self, a: AuthRecord) -> None:
        if self.db is None:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO fuel_auths VALUES(?,?,?,?,?,?,?,?,?)",
            (a.authorization_id, a.entitlement_id, a.amount_minor, int(a.approved),
             json.dumps(list(a.reasons)), a.provider_ref, a.state, a.captured_minor,
             a.issuing_transaction))
        self.db.commit()

    def _audit(self, kind: str, summary: str, e: Entitlement | None = None, **payload) -> None:
        if self.audit is None:
            return
        self.audit.append("system", kind, summary, mission_id=e.job_id if e else None,
                          party=e.driver_id if e else None, payload=payload,
                          mode=getattr(self.gateway, "mode", None))

    # ---- prepared state (outside the authorization path) -------------------
    def set_issuing_balance(self, minor: int) -> None:
        with self._lock:
            self.issuing_balance_minor = minor

    def update_job(self, job_id: str, *, status: str | None = None, driver_id: str | None = None,
                   assignment_version: int | None = None, coverage_valid: bool | None = None,
                   route: Sequence[Point] | None = None, position: Point | None = None,
                   position_ts: float | None = None) -> JobState:
        """Prepare state. Completion, deactivation or reassignment revoke unused authority."""
        with self._lock:
            j = self.jobs.setdefault(job_id, JobState(job_id))
            if status is not None:
                j.status = status
            if driver_id is not None:
                j.driver_id = driver_id
            if assignment_version is not None:
                j.assignment_version = assignment_version
            if coverage_valid is not None:
                j.coverage_valid = coverage_valid
            if route is not None:
                j.route = tuple(route)
            if position is not None:
                j.position = position
            if position_ts is not None:
                j.position_ts = position_ts
            for e in self.entitlements.values():
                if e.job_id != job_id or e.state != "active":
                    continue
                if j.status != "active":
                    self._revoke(e, f"job_{j.status}")
                elif j.driver_id != e.driver_id or j.assignment_version != e.assignment_version:
                    self._revoke(e, "reassigned")
                elif not j.coverage_valid:
                    self._revoke(e, "coverage_invalid")
            return j

    def update_policy(self, policy: FuelPolicy) -> int:
        """A new policy version invalidates all unused authority. Returns count revoked."""
        with self._lock:
            changed = policy.version != self.policy.version
            self.policy = policy
            n = 0
            if changed:
                for e in self.entitlements.values():
                    if e.state == "active":
                        self._revoke(e, "policy_changed")
                        n += 1
            return n

    def _revoke(self, e: Entitlement, reason: str) -> None:
        # Outstanding authorizations stay reserved so they can still capture/reconcile.
        e.state, e.revoke_reason = "revoked", reason
        self._save_e(e)
        self._audit("fuel.revoke", f"fuel authority {e.entitlement_id} revoked: {reason}", e,
                    entitlement_id=e.entitlement_id, reason=reason)

    def revoke(self, entitlement_id: str, reason: str = "manual") -> Entitlement:
        with self._lock:
            e = self.entitlements[entitlement_id]
            if e.state == "active":
                self._revoke(e, reason)
            return e

    # ---- issuing ------------------------------------------------------------
    def issue(self, job_id: str, driver_id: str, assignment_version: int,
              cardholder_name: str = "Trip driver") -> Entitlement:
        with self._lock:
            j = self.jobs.get(job_id)
            if j is None or j.status != "active" or j.driver_id != driver_id \
                    or j.assignment_version != assignment_version:
                raise ValueError("no active matching assignment for this job")
            if not j.coverage_valid:
                raise ValueError("coverage invalid: no fuel authority")
            den = currency_denial(self.policy.currency, self.registry)
            if den is not None:
                raise ValueError(den["reason"])
            eid = f"fe_{hashlib.sha256(f'{job_id}:{driver_id}:{assignment_version}:{self.policy.version}'.encode()).hexdigest()[:12]}"
            if eid in self.entitlements:
                return self.entitlements[eid]
            card = self.gateway.issue_card(f"card:{eid}", cardholder_name, self.policy.currency)
            self._check_live(card)
            e = Entitlement(eid, job_id, driver_id, assignment_version, card.provider_ref,
                            self.policy.version, self.policy.cap_minor)
            self.entitlements[eid] = e
            self._save_e(e)
            self._audit("fuel.issue", f"fuel entitlement {eid} issued (cap {e.cap_minor})", e,
                        entitlement_id=eid, card_ref=card.provider_ref, cap_minor=e.cap_minor,
                        policy_version=e.policy_version, creates_authority_only=True)
            return e

    @staticmethod
    def _check_live(result) -> None:
        if getattr(result, "livemode", False):
            raise LiveModeError("livemode object rejected")

    # ---- authorization decision: local state only ---------------------------
    def decide(self, authorization_id: str, card_ref: str, amount_minor: int, currency: str,
               category: str, country: str, merchant_id: str,
               now: float | None = None) -> AuthRecord:
        with self._lock:
            if authorization_id in self.auths:
                return self.auths[authorization_id]
            now = self.clock() if now is None else now
            e = next((x for x in self.entitlements.values() if x.card_ref == card_ref), None)
            reasons = self._reasons(e, amount_minor, currency, category, country, merchant_id, now)
            rec = AuthRecord(authorization_id, e.entitlement_id if e else "", amount_minor,
                             not reasons, tuple(reasons))
            if not reasons:
                assert e is not None
                e.reserved_minor += amount_minor
                rec.state = "reserved"
                self._save_e(e)
            self.auths[authorization_id] = rec
            self._save_a(rec)
            self._audit("fuel.auth", f"fuel authorization {authorization_id} "
                        f"{'approved' if rec.approved else 'declined'} ({amount_minor})", e,
                        authorization_id=authorization_id, amount_minor=amount_minor,
                        approved=rec.approved, reasons=list(rec.reasons), category=category,
                        country=country, merchant_id=merchant_id, decided_locally=True)
            return rec

    def _reasons(self, e, amount, currency, category, country, merchant_id, now) -> list[str]:
        p = self.policy
        if e is None:
            return ["unknown_card"]
        r: list[str] = []
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            return ["invalid_amount"]
        if e.state != "active":
            r.append(f"authority_revoked:{e.revoke_reason}")
        j = self.jobs.get(e.job_id)
        if j is None or j.status != "active":
            r.append("job_not_active")
        elif j.driver_id != e.driver_id or j.assignment_version != e.assignment_version:
            r.append("assignment_changed")
        if j is not None and not j.coverage_valid:
            r.append("invalid_coverage")
        if e.policy_version != p.version:
            r.append("policy_version_changed")
        if currency.lower() != p.currency:
            r.append("currency_not_permitted")
        elif currency_denial(currency, self.registry) is not None:
            r.append("currency_not_permitted:CURRENCY-1")
        if category not in p.categories:
            r.append("merchant_category_not_permitted")
        try:
            from .decision import clause_reasons, server_facts
            facts = server_facts(getattr(self, "audit", None) and self.audit, None, merchant_id, category,
                                 now if isinstance(now, (int, float)) else None)
            r.extend(x for x in clause_reasons(self.registry, facts) if x not in r)
        except Exception:  # noqa: BLE001  # a clause evaluation fault must not silently approve
            r.append("policy_clause_error")
        if country.upper() not in p.countries:
            r.append("country_not_permitted")
        if self.issuing_balance_minor is None:
            r.append("issuing_balance_unverified")
        else:
            outstanding = sum(x.reserved_minor for x in self.entitlements.values())
            if outstanding + amount > self.issuing_balance_minor:
                r.append("issuing_balance_insufficient")
        if j is not None:
            if j.position is None or j.position_ts is None \
                    or not position_fresh(j.position_ts, now, p.max_position_age_s):
                r.append("stale_position")
            stop = self.stops.get(merchant_id)
            if stop is None or not j.route:
                r.append("stop_not_on_permitted_route")
            elif (distance_to_route_m(stop, j.route) > p.max_route_distance_m
                  or (j.position is not None
                      and distance_to_route_m(j.position, j.route) > p.max_route_distance_m)):
                r.append("out_of_route")
        if e.reserved_minor + e.captured_minor + amount > e.cap_minor:
            r.append("over_limit")
        used = sum(x.reserved_minor + x.captured_minor for x in self.entitlements.values())
        if used + amount > p.budget_minor:
            r.append("aggregate_budget_exceeded")
        return r

    # ---- provider flow -------------------------------------------------------
    def attempt(self, entitlement_id: str, amount_minor: int, category: str, country: str,
                merchant_id: str, now: float | None = None) -> AuthRecord:
        """Simulated merchant attempt: decide locally, then create the provider authorization."""
        with self._lock:
            e = self.entitlements[entitlement_id]
            self._seq += 1
            auth_id = f"fa_{entitlement_id}_{self._seq}"
        rec = self.decide(auth_id, e.card_ref, amount_minor, self.policy.currency,
                          category, country, merchant_id, now)
        if not rec.approved:
            return rec
        return self._provider_authorize(rec, e)

    def _provider_authorize(self, rec: AuthRecord, e: Entitlement) -> AuthRecord:
        try:
            res = self.gateway.authorize_fuel(f"auth:{rec.authorization_id}", e.card_ref,
                                              rec.amount_minor, self.policy.currency)
            self._check_live(res)
        except LiveModeError:
            self._unreserve(rec, e, "blocked", ("livemode_rejected",))
            raise
        except OutcomeUnknown as exc:  # keep the reservation: money state is unknown
            with self._lock:
                rec.approved, rec.state, rec.reasons = False, "unknown", ("provider_outcome_unknown",)
                self._save_a(rec)
            self._audit("fuel.auth.unknown", f"provider outcome unknown for {rec.authorization_id}",
                        e, authorization_id=rec.authorization_id, error=str(exc)[:200])
            return rec
        except (CapabilityUnavailable, GatewayError) as exc:
            self._unreserve(rec, e, "released", ("provider_unavailable",))
            self._audit("fuel.auth.failed", f"provider rejected {rec.authorization_id}", e,
                        authorization_id=rec.authorization_id, error=str(exc)[:200])
            return rec
        with self._lock:
            rec.provider_ref = res.provider_ref
            if res.approved and rec.state == "unknown":
                rec.state = "reserved"
            self._save_a(rec)
        if not res.approved:
            self._unreserve(rec, e, "released", ("provider_declined",))
        return rec

    def _unreserve(self, rec: AuthRecord, e: Entitlement, state: str, reasons: tuple) -> None:
        with self._lock:
            if rec.state in ("reserved", "unknown"):
                e.reserved_minor -= rec.amount_minor
            rec.approved, rec.state, rec.reasons = False, state, reasons
            self._save_e(e)
            self._save_a(rec)
        self._audit("fuel.unreserve", f"reservation for {rec.authorization_id} freed ({state})", e,
                    authorization_id=rec.authorization_id, reasons=list(reasons))

    def resolve_unknown(self, authorization_id: str) -> AuthRecord:
        """Retry the provider authorization with the SAME operation id (idempotent)."""
        with self._lock:
            rec = self.auths[authorization_id]
            if rec.state != "unknown":
                return rec
            e = self.entitlements[rec.entitlement_id]
            rec.approved = True
        return self._provider_authorize(rec, e)

    def capture(self, authorization_id: str, amount_minor: int | None = None) -> AuthRecord:
        """Capture; allowed even after revocation so outstanding authorizations reconcile."""
        with self._lock:
            rec = self.auths[authorization_id]
            if rec.state == "captured":
                return rec
            if rec.state != "reserved":
                raise ValueError(f"authorization is {rec.state}, cannot capture")
            amt = rec.amount_minor if amount_minor is None else amount_minor
            if amt <= 0 or amt > rec.amount_minor:
                raise ValueError("capture must be positive and not exceed the authorization")
            res = self.gateway.capture_fuel(f"capture:{authorization_id}", rec.provider_ref, amt)
            self._check_live(res)
            e = self.entitlements[rec.entitlement_id]
            e.reserved_minor -= rec.amount_minor
            e.captured_minor += amt
            e.spending = True
            rec.state, rec.captured_minor = "captured", amt
            rec.issuing_transaction = res.provider_ref
            self._save_e(e)
            self._save_a(rec)
            self._audit("fuel.capture", f"fuel authorization {authorization_id} captured ({amt})",
                        e, authorization_id=authorization_id, captured_minor=amt,
                        issuing_ref=res.provider_ref, provider_status=res.status,
                        livemode=res.livemode)
            return rec

    def release(self, authorization_id: str) -> AuthRecord:
        with self._lock:
            rec = self.auths[authorization_id]
            if rec.state != "reserved":
                return rec
            e = self.entitlements[rec.entitlement_id]
            if rec.provider_ref:
                # Void the provider authorization first. Headroom is freed only when the
                # provider confirms the authorization is no longer live.
                res = self.gateway.release_authorization(f"release:{authorization_id}",
                                                         rec.provider_ref)
                self._check_live(res)
                if res.status not in RELEASED_STATUSES:
                    rec.release_unresolved, rec.release_status = True, str(res.status)
                    self._audit("fuel.release.unresolved",
                                f"release of {authorization_id} unresolved: provider status "
                                f"{res.status}; reservation kept", e,
                                authorization_id=authorization_id, provider_ref=rec.provider_ref,
                                provider_status=res.status, reserved_minor=rec.amount_minor)
                    return rec
            e.reserved_minor -= rec.amount_minor
            rec.state = "released"
            rec.release_unresolved, rec.release_status = False, "released"
            self._save_e(e)
            self._save_a(rec)
            self._audit("fuel.release", f"fuel authorization {authorization_id} released", e,
                        authorization_id=authorization_id, provider_ref=rec.provider_ref,
                        provider_reversed=bool(rec.provider_ref))
            return rec

    def reconcile(self, entitlement_id: str) -> dict:
        with self._lock:
            e = self.entitlements[entitlement_id]
            recs = [a for a in self.auths.values() if a.entitlement_id == entitlement_id]
            res = sum(a.amount_minor for a in recs if a.state in ("reserved", "unknown"))
            cap = sum(a.captured_minor for a in recs if a.state == "captured")
            return {"consistent": res == e.reserved_minor and cap == e.captured_minor,
                    "reserved_minor": e.reserved_minor, "captured_minor": e.captured_minor,
                    "transactions": [a.issuing_transaction for a in recs if a.issuing_transaction]}

    # ---- webhook ---------------------------------------------------------------
    def handle_webhook(self, payload: bytes, sig_header: str | None, secret: str | None,
                       now: float | None = None) -> dict:
        """Verify the Stripe signature, then answer issuing_authorization.request locally."""
        if not secret:
            raise CapabilityUnavailable(
                "issuing_webhook", "no webhook signing secret configured",
                "Set STRIPE_WEBHOOK_SECRET to the test-mode whsec_ value from the Stripe CLI "
                "or Dashboard (test mode).")
        verify_signature(payload, sig_header, secret, self.clock() if now is None else now)
        event = json.loads(payload)
        check_livemode(event)  # recursive: event, data.object and every nested object
        obj = event.get("data", {}).get("object", {})
        if event.get("type") != "issuing_authorization.request":
            return {"handled": False, "type": event.get("type")}
        m = obj.get("merchant_data", {})
        card = obj.get("card", {})
        rec = self.decide(obj["id"], card.get("id", "") if isinstance(card, dict) else card,
                          int(obj.get("pending_request", {}).get("amount", obj.get("amount", 0))),
                          obj.get("currency", ""), str(m.get("category_code", "")),
                          m.get("country", ""), m.get("network_id", ""), now)
        return {"handled": True, "approved": rec.approved, "reasons": list(rec.reasons)}


def sign_payload(payload: bytes, secret: str, ts: int) -> str:
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify_signature(payload: bytes, header: str | None, secret: str, now: float,
                     tolerance_s: int = WEBHOOK_TOLERANCE_S) -> None:
    if not header:
        raise SignatureError("missing signature header")
    parts = [p.split("=", 1) for p in header.split(",") if "=" in p]
    ts = next((v for k, v in parts if k == "t"), None)
    sigs = [v for k, v in parts if k == "v1"]
    if ts is None or not ts.isdigit() or not sigs:
        raise SignatureError("malformed signature header")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise SignatureError("signature mismatch")
    if abs(now - int(ts)) > tolerance_s:
        raise SignatureError("timestamp outside tolerance")
