"""Stripe sandbox capability probe.  Run: uv run python -m treasury.probe

Reports payments, Connect transfers, Issuing and invoicing separately.
Nothing is marked "ok" unless the provider actually returned the expected state.
Output is redacted; secrets never appear.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any

import stripe

from treasury.currency import DEFAULT_CURRENCY
from treasury.gateway import CapabilityUnavailable, GatewayError, LiveModeError
from treasury.stripe_gateway import (
    SETUP,
    StripeGateway,
    key_kind,
    redact,
    to_plain,
    validate_test_key,
)

CURRENCY = DEFAULT_CURRENCY  # settlement currency comes from the org policy (CURRENCY-1)
CAPABILITIES = ("payments", "connect_transfers", "issuing", "invoicing")
PROBE_MINOR = 100


def _cap(status: str, detail: str = "", setup: str = "", evidence: dict | None = None,
         funding: dict | None = None) -> dict:
    return {"status": status, "detail": detail, "setup": setup,
            "evidence": evidence or {}, "funding": funding or {}}


def _money(items: Any) -> dict:
    return {str(i.get("currency")): int(i.get("amount") or 0) for i in (items or [])}


def _balances(client: Any) -> dict:
    """Payments and Issuing funding are separate; pending is never 'available'."""
    try:
        b = to_plain(client.balance.retrieve())
    except Exception as e:  # noqa: BLE001
        return {"error": str(redact(str(e)))[:200]}
    issuing = (b.get("issuing") or {}).get("available") if hasattr(b, "get") else None
    return {"payments": {"available": _money(b.get("available")),
                         "pending": _money(b.get("pending"))},
            "issuing": {"available": _money(issuing)}}


def _unavailable_all(reason: str) -> dict:
    return {c: _cap("unavailable", reason, SETUP[c]) for c in CAPABILITIES}


def _guard(fn) -> dict:
    try:
        return fn()
    except CapabilityUnavailable as e:
        return _cap("unavailable", e.reason, e.setup)
    except LiveModeError:
        raise
    except GatewayError as e:
        return _cap("failed", str(redact(str(e)))[:300])
    except stripe.PermissionError as e:
        return _cap("unavailable", str(redact(str(e)))[:300],
                    "The key lacks permission for this endpoint. If this is an unclaimed sandbox, "
                    "run `stripe sandbox claim` (or open the claim URL) for a full-access key.")
    except stripe.StripeError as e:
        return _cap("failed", str(redact(str(e)))[:300])


def _payments(gw: StripeGateway, op: str, bal: dict) -> dict:
    pay = gw.create_payment(f"{op}-pay", PROBE_MINOR, CURRENCY)
    ref = gw.refund(f"{op}-refund", pay.provider_ref)
    ok = pay.status == "succeeded" and ref.status in ("succeeded", "pending")
    return _cap("ok" if ok else "failed", f"payment {pay.status}, refund {ref.status}",
                evidence={"payment_intent": pay.provider_ref, "payment_status": pay.status,
                          "refund": ref.provider_ref, "refund_status": ref.status,
                          "currency": pay.currency, "livemode": pay.livemode},
                funding=bal.get("payments", {}))


def _connect_accounts(client: Any, env: dict) -> list[str]:
    explicit = [env.get("STRIPE_CONNECT_ACCOUNT_A"), env.get("STRIPE_CONNECT_ACCOUNT_B")]
    if all(explicit):
        return list(dict.fromkeys(explicit))  # type: ignore[arg-type]
    out = []
    for a in to_plain(client.accounts.list(params={"limit": 20})).get("data", []):
        if (a.get("capabilities") or {}).get("transfers") == "active":
            out.append(a["id"])
    return out


def _connect(gw: StripeGateway, op: str, env: dict) -> dict:
    accounts = _connect_accounts(gw.client, env)
    if len(accounts) < 2:
        return _cap("unavailable",
                    f"need two distinct connected accounts with transfers active; "
                    f"found {len(accounts)}", SETUP["connect_transfers"])
    a, b = accounts[:2]
    group = f"{op}-group"
    # Separate charges and transfers: one platform charge, then one transfer per recipient
    # carrying source_transaction and the shared transfer_group.
    charge = gw.create_payment(f"{op}-topup", 5000, CURRENCY,
                               payment_method="pm_card_bypassPending", transfer_group=group)
    funding = _balances(gw.client).get("payments", {})
    if funding.get("available", {}).get(CURRENCY, 0) < 2 * PROBE_MINOR:
        return _cap("partial", "funds are pending, not available; transfers not attempted",
                    SETUP["connect_transfers"], funding=funding)
    src = charge.charge_ref or None
    t1 = gw.transfer(f"{op}-t1", PROBE_MINOR, CURRENCY, a, source_transaction=src,
                     transfer_group=group)
    t2 = gw.transfer(f"{op}-t2", PROBE_MINOR, CURRENCY, b, source_transaction=src,
                     transfer_group=group)
    return _cap("ok", "two transfers created to distinct accounts",
                evidence={"transfers": [t1.provider_ref, t2.provider_ref],
                          "destinations": [a, b], "currency": CURRENCY,
                          "source_transaction": src, "transfer_group": group,
                          "states": [t1.status, t2.status]}, funding=funding)


def _issuing(gw: StripeGateway, op: str, bal: dict) -> dict:
    card = gw.issue_card(f"{op}-card", "Probe Agent")
    funding = _balances(gw.client).get("issuing", bal.get("issuing", {}))
    auth = gw.authorize_fuel(f"{op}-auth", card.provider_ref, PROBE_MINOR)
    ev = {"card": card.provider_ref, "cardholder": card.cardholder_ref,
          "authorization": auth.provider_ref, "auth_status": auth.status,
          "approved": auth.approved, "currency": auth.currency}
    if not auth.approved:
        return _cap("partial", "authorization declined; Issuing balance likely unfunded. "
                    "Fund via push funding instructions", SETUP["issuing"],
                    evidence=ev, funding=funding)
    cap = gw.capture_fuel(f"{op}-cap", auth.provider_ref)
    ev.update(capture_status=cap.status)
    return _cap("ok" if cap.status == "captured" else "failed",
                f"capture {cap.status}", evidence=ev, funding=funding)


def _invoicing(gw: StripeGateway, op: str) -> dict:
    inv = gw.create_invoice(f"{op}-inv", PROBE_MINOR, CURRENCY, "probe@example.com", "Probe invoice")
    return _cap("ok" if inv.status == "paid" else "failed", f"invoice {inv.status}",
                evidence={"invoice": inv.provider_ref, "status": inv.status,
                          "currency": inv.currency, "amount_minor": inv.amount_minor})


def run_probe(api_key: str | None = None, client: Any = None,
              env: dict | None = None, audit: Any = None) -> dict:
    env = dict(os.environ) if env is None else env
    key = api_key if api_key is not None else env.get("STRIPE_SECRET_KEY")
    if client is None:
        try:
            validate_test_key(key)
        except CapabilityUnavailable as e:
            return {"mode": "stripe_test", "configured": False, "account": {},
                    "capabilities": _unavailable_all(e.reason)}
    gw = StripeGateway(api_key=key, client=client, audit=audit)
    op = f"probe-{uuid.uuid4().hex[:8]}"
    account: dict = {}
    try:
        acct = to_plain(gw.client.accounts.retrieve_current())
        account = {k: acct.get(k) for k in ("id", "country", "default_currency")}
    except Exception as e:  # noqa: BLE001
        account = {"error": str(redact(str(e)))[:200]}
    bal = _balances(gw.client)
    caps = {
        "payments": _guard(lambda: _payments(gw, op, bal)),
        "connect_transfers": _guard(lambda: _connect(gw, op, env)),
        "issuing": _guard(lambda: _issuing(gw, op, bal)),
        "invoicing": _guard(lambda: _invoicing(gw, op)),
    }
    return redact({"mode": "stripe_test", "configured": True, "livemode": False,
                   "key_kind": key_kind(key), "account": account, "balances": bal, "capabilities": caps})


def exit_code(report: dict) -> int:
    statuses = {c["status"] for c in report["capabilities"].values()}
    if "failed" in statuses:
        return 1
    return 0 if statuses == {"ok"} else 2


def main() -> int:
    import sqlite3

    from treasury.audit import AuditLog
    db = sqlite3.connect(os.environ.get("TREASURY_DB", "treasury.sqlite"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    try:
        report = run_probe(audit=AuditLog(db))
    except LiveModeError as e:
        print(json.dumps({"error": str(e), "capabilities": {}}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
