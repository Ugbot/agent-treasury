"""Provision the Stripe SANDBOX for the demo. Idempotent: safe to re-run.

    uv run python scripts/bootstrap_sandbox.py

Creates (test mode only): two Accounts-v2 recipient accounts (merchant, driver) with stripe_balance.stripe_transfers,
funds the platform balance (pm_card_bypassPending), an Issuing cardholder + virtual card, and
needs an open v2 financial account (else reported as blocked). Ids (not secrets) go to the gitignored
`.treasury-state.json`; they are picked up automatically as STRIPE_CONNECT_ACCOUNT_A/B etc.

Each step reports ok / blocked with the exact setup instruction; nothing is faked. Exit code 0
only if every step is ok, 2 if something is blocked, 1 on a refusal (no key, live key).
No secret value is ever printed.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
import warnings

from treasury.settings import load_env, read_state, write_state

load_env(force=False)
warnings.filterwarnings("ignore")

from treasury.audit import AuditLog
from treasury.currency import DEFAULT_CURRENCY
from treasury.gateway import CapabilityUnavailable, GatewayError, LiveModeError
from treasury.stripe_gateway import SETUP, StripeGateway, key_kind, redact

PLATFORM_FUND_MINOR = 100_000
ISSUING_TOPUP_MINOR = 50_000
ROLES = {"merchant": "connect_account_a", "driver": "connect_account_b"}

KEY_HELP = {
    "missing": "STRIPE_SECRET_KEY is not set. Put a standard sandbox secret key (sk_test_...) in .env "
               "(https://dashboard.stripe.com/test/apikeys).",
    "live": "A LIVE key was supplied. This project is test mode only; refusing to run.",
    "unknown": "Unrecognised key format. Use a standard sandbox secret key (sk_test_...).",
}
RESTRICTED_WARNING = (
    "Key is a limited sandbox key (rkcs_test_/rk_test_). Creating Connect accounts, Issuing "
    "cardholders/cards and top-ups normally needs a standard sk_test_ key: create one at "
    "https://dashboard.stripe.com/test/apikeys and set STRIPE_SECRET_KEY. Trying anyway; blocked "
    "steps are reported below.")


class Report:
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []

    def add(self, step: str, status: str, detail: str = "") -> None:
        self.rows.append((step, status, str(redact(detail))))
        print(f"[{status.upper():7}] {step}" + (f": {redact(detail)}" if detail else ""))

    @property
    def blocked(self) -> bool:
        return any(s != "ok" for _, s, _ in self.rows)


def guarded(rep: Report, step: str, fn):
    """Run fn; map provider access problems to a precise 'blocked' row. Returns result or None."""
    try:
        return fn()
    except LiveModeError as e:
        rep.add(step, "refused", str(e))
        raise
    except CapabilityUnavailable as e:
        rep.add(step, "blocked", f"{e.reason}\n           setup: {e.setup}")
    except GatewayError as e:
        rep.add(step, "failed", str(e))
    return None


def wait_transfers_active(gw: StripeGateway, acct_id: str, timeout_s: float) -> tuple[bool, dict]:
    end = time.time() + timeout_s
    while True:
        a = gw.retrieve_account(acct_id)
        status = (a.get("capabilities") or {}).get("transfers")
        if status == "active" or time.time() >= end:
            return status == "active", a
        time.sleep(2)


def bootstrap(gw: StripeGateway, state: dict, rep: Report, *, wait_s: float = 20.0,
              state_path=None) -> dict:
    cur = DEFAULT_CURRENCY

    # 1. Connect accounts
    for role, skey in ROLES.items():
        step = f"connect account ({role})"
        acct_id = state.get(skey)
        acct = None
        if acct_id:
            acct = guarded(rep, step, lambda acct_id=acct_id: gw.retrieve_account(acct_id))
        else:
            acct = guarded(rep, step, lambda role=role: gw.create_connect_account(
                f"bootstrap-account-{role}-v3", role))
        if acct is None:
            continue
        state[skey] = acct["id"]
        write_state(state, state_path)
        active, acct = wait_transfers_active(gw, acct["id"], wait_s)
        if active:
            rep.add(step, "ok", f"{acct['id']} transfers=active")
        else:
            due = (acct.get("requirements") or {}).get("currently_due") or []
            rep.add(step, "blocked", f"{acct['id']} transfers="
                    f"{(acct.get('capabilities') or {}).get('transfers')}; currently_due={due[:6]}\n"
                    f"           setup: {SETUP['connect_transfers']}")

    # 2. platform balance
    if state.get("platform_funded") and not os.environ.get("TREASURY_REFUND_FUND"):
        rep.add("fund platform balance", "ok", "already funded (state file)")
    else:
        pay = guarded(rep, "fund platform balance", lambda: gw.create_payment(
            "bootstrap-fund-v1", PLATFORM_FUND_MINOR, cur, payment_method="pm_card_bypassPending"))
        if pay is not None:
            ok = pay.status == "succeeded"
            if ok:
                state["platform_funded"] = pay.provider_ref
                write_state(state, state_path)
            rep.add("fund platform balance", "ok" if ok else "failed",
                    f"{pay.provider_ref} {pay.status} {pay.amount_minor} {pay.currency}")

    # 3. Issuing card
    if state.get("issuing_card"):
        rep.add("issuing cardholder + card", "ok", f"{state['issuing_card']} (state file)")
    else:
        card = guarded(rep, "issuing cardholder + card",
                       lambda: gw.issue_card("bootstrap-card-v3", "Trip Driver", cur))
        if card is not None:
            state["issuing_card"], state["issuing_cardholder"] = card.provider_ref, card.cardholder_ref
            write_state(state, state_path)
            rep.add("issuing cardholder + card", "ok",
                    f"{card.provider_ref} cardholder={card.cardholder_ref} last4={card.last4}")

    # 4. Issuing balance top-up
    if state.get("issuing_topup"):
        rep.add("fund issuing balance", "ok", f"{state['issuing_topup']} (state file)")
    else:
        top = guarded(rep, "fund issuing balance",
                      lambda: gw.topup_issuing("bootstrap-topup-v3", ISSUING_TOPUP_MINOR, cur))
        if top is not None:
            if top.get("status") in ("succeeded", "pending"):
                state["issuing_topup"] = top["id"]
                write_state(state, state_path)
            rep.add("fund issuing balance",
                    "ok" if top.get("status") == "succeeded" else "pending",
                    f"{top['id']} status={top.get('status')} "
                    f"destination_balance={top.get('destination_balance')}")
    return state


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wait", type=float, default=20.0, help="seconds to wait for capabilities")
    args = ap.parse_args(argv)
    key = os.environ.get("STRIPE_SECRET_KEY")
    kind = key_kind(key)
    if kind in KEY_HELP:
        print(KEY_HELP[kind])
        return 1
    if kind in ("restricted_sandbox", "restricted"):
        print(RESTRICTED_WARNING)
    db = sqlite3.connect(os.environ.get("TREASURY_DB", "treasury.sqlite"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    gw = StripeGateway(audit=AuditLog(db))
    rep = Report()
    try:
        acct = guarded(rep, "platform account", lambda: gw._call(
            "payments", lambda: gw.client.accounts.retrieve_current(),
            action="accounts.retrieve_current"))
        if acct is not None:
            rep.add("platform account", "ok" if acct.get("country") == "US" else "warn",
                    f"{acct.get('id')} country={acct.get('country')} "
                    f"(Issuing top-ups and this flow assume a US account)")
        state = bootstrap(gw, read_state(), rep, wait_s=args.wait)
    except LiveModeError:
        return 1
    print("\nState written to .treasury-state.json (ids only):",
          sorted(k for k in state if state[k]))
    if rep.blocked:
        print("\nBLOCKED steps above need the listed setup; re-run this script afterwards (idempotent).")
        return 2
    print("\nSandbox ready. Next: uv run python -m treasury.probe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
