"""Stream B: real-money plumbing tested against a fake Stripe client (no network)."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
import stripe

from treasury import settings
from treasury.audit import AuditLog
from treasury.fuel import FuelService
from treasury.gateway import (
    CapabilityUnavailable,
    GatewayError,
    LiveModeError,
    MockGateway,
    OutcomeUnknown,
)
from treasury.geo import Point
from treasury.proof import SettlementService, seed_order
from treasury.stripe_gateway import StripeGateway, key_kind, redact

KEY = "sk_test_" + "B" * 24


class Node:
    def __init__(self, handler, path=()):
        self._h, self._p = handler, path

    def __getattr__(self, name):
        return Node(self._h, (*self._p, name))

    def __call__(self, *a, **k):
        return self._h(".".join(self._p), a, k)


class FakeStripe(Node):
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}
        super().__init__(self._handle)

    def _handle(self, path, a, k):
        self.calls.append((path, a, k))
        r = self.responses.get(path)
        if isinstance(r, Exception):
            raise r
        if callable(r):
            return r(a, k)
        n = len(self.calls)
        return {"id": f"x_{n}", "livemode": False, "status": "succeeded", **(r or {})}

    def paths(self):
        return [c[0] for c in self.calls]


def v2_handler(caps="active", open_fa=True, due=()):
    """Fake /v2 endpoints: recipient accounts and financial accounts."""
    def h(a, k):
        method, path, body = a[0], a[1], a[2]
        if "financial_accounts" in path:
            return {"data": [{"id": "fa_1", "status": "open" if open_fa else "pending"}]}
        if method == "POST":
            role = body["metadata"]["treasury_role"]
            aid = f"acct_{role}"
        else:
            aid = path.split("/v2/core/accounts/")[1].split("?")[0]
        return {"id": aid, "livemode": False, "identity": {"country": "US"},
                "configuration": {"recipient": {"capabilities": {"stripe_balance": {
                    "stripe_transfers": {"status": caps}}}}},
                "requirements": {"entries": [{"description": d} for d in due]}}
    return h


def gw_with(responses=None):
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    audit = AuditLog(db)
    fake = FakeStripe(responses)
    return StripeGateway(client=fake, audit=audit), fake, audit, db


# ---- .env / settings -----------------------------------------------------------------------
def test_dotenv_loads_without_overriding_and_merges_state(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("TREASURY_TEST_A=from_file\nTREASURY_TEST_B=from_file\n")
    st = tmp_path / "state.json"
    st.write_text(json.dumps({"connect_account_a": "acct_A", "connect_account_b": "acct_B"}))
    monkeypatch.setattr(settings, "STATE_FILE", tmp_path / "none.json")
    monkeypatch.setenv("TREASURY_TEST_B", "from_env")
    monkeypatch.delenv("TREASURY_TEST_A", raising=False)
    monkeypatch.delenv("STRIPE_CONNECT_ACCOUNT_A", raising=False)
    monkeypatch.setenv("STRIPE_CONNECT_ACCOUNT_B", "acct_explicit")
    assert settings.load_env(env, force=True)
    import os
    assert os.environ["TREASURY_TEST_A"] == "from_file"
    assert os.environ["TREASURY_TEST_B"] == "from_env"  # real env wins
    settings.load_state_env(st)
    assert os.environ["STRIPE_CONNECT_ACCOUNT_A"] == "acct_A"
    assert os.environ["STRIPE_CONNECT_ACCOUNT_B"] == "acct_explicit"
    monkeypatch.delenv("TREASURY_TEST_A")
    monkeypatch.delenv("STRIPE_CONNECT_ACCOUNT_A")


def test_dotenv_skipped_under_pytest():
    assert settings.load_env() is False  # never picks up a real key in tests


# ---- redaction / key kinds ---------------------------------------------------------------------
@pytest.mark.parametrize("text", ["rkcs_test_AbC123", "rkcs_te****abcd", "sk_test_abc", "rk_live_x1",
                                  "whsec_abc", "pi_1_secret_xyz"])
def test_redacts_all_key_shapes(text):
    assert "[redacted-key]" in redact(f"err: {text} end")
    assert text not in redact(f"err: {text} end")


def test_redaction_leaves_setup_text_and_words():
    assert redact("use sk_test_... and risk_level") == "use sk_test_... and risk_level"


def test_key_kind_by_prefix_only():
    assert key_kind(KEY) == "standard"
    assert key_kind("rkcs_test_zzz") == "restricted_sandbox"
    assert key_kind("rk_test_zzz") == "restricted"
    assert key_kind("sk_live_zzz") == "live" and key_kind("rk_live_zzz") == "live"
    assert key_kind(None) == "missing" and key_kind("nope") == "unknown"
    with pytest.raises(LiveModeError):
        StripeGateway(api_key="rkcs_live_abc")


# ---- audit of every Stripe call ------------------------------------------------------------------
def test_every_call_is_audited_with_ids_idempotency_and_livemode():
    gw, fake, audit, _ = gw_with({"payment_intents.create": {"id": "pi_1", "latest_charge": "ch_1",
                                                           "amount": 500, "currency": "usd"}})
    pay = gw.create_payment("op1", 500, "usd", transfer_group="order_o1")
    assert pay.charge_ref == "ch_1" and pay.transfer_group == "order_o1"
    params = fake.calls[0][2]["params"]
    assert params["transfer_group"] == "order_o1"
    assert fake.calls[0][2]["options"] == {"idempotency_key": "treasury-payment-op1"}
    e = audit.query(kind="stripe.call")[0]
    assert e["stripe"]["ids"][:2] == ["pi_1", "ch_1"]
    assert e["stripe"]["idempotency_key"] == "treasury-payment-op1"
    assert e["stripe"]["livemode"] is False and e["stripe"]["outcome"] == "ok"
    assert e["mode"] == "stripe_test" and audit.verify()["ok"]


def test_failures_unknowns_and_livemode_are_audited():
    gw, _, audit, _ = gw_with({
        "refunds.create": stripe.InvalidRequestError(f"bad {KEY}", "p"),
        "transfers.create": stripe.APIConnectionError("timeout"),
        "payment_intents.create": {"id": "pi_live", "livemode": True},
        "v2_request": v2_handler(),
        "issuing.cards.create": stripe.PermissionError("Issuing is not enabled")})
    with pytest.raises(GatewayError):
        gw.refund("r1", "pi_1")
    with pytest.raises(OutcomeUnknown):
        gw.transfer("t1", 10, "usd", "acct_1")
    with pytest.raises(LiveModeError):
        gw.create_payment("p1", 10, "usd")
    with pytest.raises(CapabilityUnavailable):
        gw.issue_card("c1", "Trip Driver")
    outcomes = [(e["stripe"]["op"], e["stripe"]["outcome"]) for e in audit.query(kind="stripe.call")]
    assert ("r1", "failed") in outcomes and ("t1", "unknown") in outcomes
    assert ("p1", "livemode_rejected") in outcomes and ("c1", "unavailable") in outcomes
    assert KEY not in json.dumps(audit.query())


def test_transfer_carries_source_transaction_and_group():
    gw, fake, _, _ = gw_with({"transfers.create": {"id": "tr_1", "amount": 700, "currency": "usd",
                                                 "transfer_group": "order_o1", "reversed": False}})
    t = gw.transfer("o1:merchant", 700, "usd", "acct_m", source_transaction="ch_1",
                    transfer_group="order_o1")
    p = fake.calls[0][2]["params"]
    assert p["source_transaction"] == "ch_1" and p["transfer_group"] == "order_o1"
    assert t.status == "paid" and t.source_transaction == "ch_1"
    assert fake.calls[0][2]["options"]["idempotency_key"] == "treasury-transfer-o1:merchant"


def test_release_authorization_reverses_then_falls_back_to_expire():
    gw, fake, _, _ = gw_with({"test_helpers.issuing.authorizations.reverse":
                              stripe.InvalidRequestError("cannot reverse", "p"),
                              "test_helpers.issuing.authorizations.expire": {"status": "reversed"}})
    r = gw.release_authorization("rel1", "iauth_1")
    assert r.status == "reversed"
    assert fake.paths() == ["test_helpers.issuing.authorizations.reverse",
                            "test_helpers.issuing.authorizations.expire"]


# ---- separate charges and transfers, independent legs ---------------------------------------------
def test_settlement_funds_once_then_two_independent_transfers_share_group():
    mock = MockGateway()
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    audit = AuditLog(db)
    svc = SettlementService(mock, audit=audit)
    o = svc.add_order(seed_order())
    f1 = svc.fund_order(o.id)
    assert svc.fund_order(o.id) == f1  # idempotent: one charge
    assert [c for c in mock.calls if c[0] == "payment"] == [("payment", f"fund:{o.id}")]
    nonce = svc.issue_nonce(o.id, 100.0)
    out = svc.release(o.id, driver_id=o.assigned_driver, position=o.destination, position_ts=95.0,
                      recipient_id=o.recipient_id, nonce=nonce, now=100.0)
    assert out["status"] == "released"
    legs = out["legs"]
    assert legs["merchant"]["provider_ref"] != legs["driver"]["provider_ref"]
    assert {legs[k]["transfer_group"] for k in legs} == {f"order_{o.id}"}
    assert {legs[k]["source_transaction"] for k in legs} == {f1["charge_ref"]} and f1["charge_ref"]
    kinds = [e["kind"] for e in audit.query(mission_id=o.id)]
    assert kinds.count("delivery.leg") == 2 and "delivery.proof" in kinds and "delivery.fund" in kinds


# ---- fuel: persistence, release, livemode, unknown ------------------------------------------------
ROUTE = (Point(51.5000, -0.1000), Point(51.5100, -0.1000))
STOP = Point(51.5050, -0.1000)
NOW = 1000.0


def fuel(db, gw=None, audit=None):
    svc = FuelService(gw or MockGateway(), stops={"shell": STOP}, clock=lambda: NOW,
                      issuing_balance_minor=5000, db=db, audit=audit)
    svc.update_job("j1", status="active", driver_id="d1", assignment_version=1, route=ROUTE,
                   position=Point(51.5049, -0.1000), position_ts=NOW - 5)
    return svc


def attempt(svc, e, amt=500):
    return svc.attempt(e.entitlement_id, amt, "5541", "GB", "shell")


def test_fuel_counters_survive_restart_so_cap_cannot_reset():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    svc = fuel(db)
    e = svc.issue("j1", "d1", 1)
    r = attempt(svc, e, 500)
    svc.capture(r.authorization_id)
    r2 = attempt(svc, e, 200)  # reserved, not captured
    assert r2.approved
    svc2 = fuel(db)  # "restart"
    e2 = svc2.entitlements[e.entitlement_id]
    assert (e2.captured_minor, e2.reserved_minor) == (500, 200)
    r3 = attempt(svc2, e2, 200)  # 500+200+200 > cap 800
    assert not r3.approved and "over_limit" in r3.reasons
    assert r3.authorization_id != r.authorization_id != r2.authorization_id  # seq restored
    assert svc2.reconcile(e.entitlement_id)["consistent"]


def test_release_voids_provider_authorization_and_frees_cap():
    gw = MockGateway()
    svc = fuel(None, gw)
    e = svc.issue("j1", "d1", 1)
    r = attempt(svc, e, 800)
    assert e.remaining_minor == 0
    svc.release(r.authorization_id)
    assert ("release", f"release:{r.authorization_id}") in gw.calls
    assert e.remaining_minor == 800 and svc.auths[r.authorization_id].state == "released"


def test_release_failure_keeps_cap_reserved():
    class NoRelease(MockGateway):
        def release_authorization(self, operation_id, authorization_ref):
            raise GatewayError("provider down")

    svc = fuel(None, NoRelease())
    e = svc.issue("j1", "d1", 1)
    r = attempt(svc, e, 300)
    with pytest.raises(GatewayError):
        svc.release(r.authorization_id)
    assert e.reserved_minor == 300 and svc.auths[r.authorization_id].state == "reserved"


def test_livemode_authorize_result_is_rejected_and_reservation_freed():
    class Live(MockGateway):
        def authorize_fuel(self, *a, **k):
            r = super().authorize_fuel(*a, **k)
            return type(r)(**{**r.__dict__, "livemode": True})

    svc = fuel(None, Live())
    e = svc.issue("j1", "d1", 1)
    with pytest.raises(LiveModeError):
        attempt(svc, e, 300)
    assert e.reserved_minor == 0 and e.captured_minor == 0


def test_livemode_capture_result_is_rejected_and_not_counted():
    class Live(MockGateway):
        def capture_fuel(self, *a, **k):
            r = super().capture_fuel(*a, **k)
            return type(r)(**{**r.__dict__, "livemode": True})

    svc = fuel(None, Live())
    e = svc.issue("j1", "d1", 1)
    r = attempt(svc, e, 300)
    with pytest.raises(LiveModeError):
        svc.capture(r.authorization_id)
    assert e.captured_minor == 0 and e.reserved_minor == 300


def test_unknown_provider_outcome_keeps_reservation_then_resolves():
    class Flaky(MockGateway):
        fail = True

        def authorize_fuel(self, *a, **k):
            if self.fail:
                raise OutcomeUnknown("timeout")
            return super().authorize_fuel(*a, **k)

    gw = Flaky()
    svc = fuel(None, gw)
    e = svc.issue("j1", "d1", 1)
    r = attempt(svc, e, 300)
    assert r.state == "unknown" and e.reserved_minor == 300
    gw.fail = False
    r = svc.resolve_unknown(r.authorization_id)
    assert r.state == "reserved" and r.approved and r.provider_ref
    assert svc.reconcile(e.entitlement_id)["consistent"]


def test_fuel_effects_are_audited():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    audit = AuditLog(db)
    svc = fuel(db, audit=audit)
    e = svc.issue("j1", "d1", 1)
    r = attempt(svc, e, 300)
    svc.capture(r.authorization_id)
    r2 = attempt(svc, e, 100)
    svc.release(r2.authorization_id)
    kinds = [x["kind"] for x in audit.query()]
    for k in ("fuel.issue", "fuel.auth", "fuel.capture", "fuel.release"):
        assert k in kinds
    assert audit.verify()["ok"]


# ---- bootstrap script -------------------------------------------------------------------------------
def load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_sandbox.py"
    spec = importlib.util.spec_from_file_location("bootstrap_sandbox", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bootstrap_refuses_missing_and_live_keys(monkeypatch, capsys):
    b = load_script()
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert b.main([]) == 1
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_" + "x" * 20)
    assert b.main([]) == 1
    assert "sk_live_" + "x" * 20 not in capsys.readouterr().out


def test_bootstrap_creates_everything_and_is_idempotent(tmp_path):
    b = load_script()
    responses = {
        "v2_request": v2_handler(),
        "issuing.cards.create": {"id": "ic_1", "last4": "4242"},
        "issuing.cardholders.create": {"id": "ich_1"},
        "topups.create": {"id": "tu_1", "status": "succeeded"},
    }
    gw, fake, audit, _ = gw_with(responses)
    sp = tmp_path / "state.json"
    rep = b.Report()
    state = b.bootstrap(gw, {}, rep, wait_s=0, state_path=sp)
    assert state["connect_account_a"] == "acct_merchant" and state["connect_account_b"] == "acct_driver"
    assert state["issuing_card"] == "ic_1"
    card = next(c for c in fake.calls if c[0] == "issuing.cards.create")[2]["params"]
    assert card["financial_account_v2"] == "fa_1"
    pay = next(c for c in fake.calls if c[0] == "payment_intents.create")[2]["params"]
    assert pay["payment_method"] == "pm_card_bypassPending"
    n = len(fake.calls)
    rep2 = b.Report()
    b.bootstrap(gw, json.loads(sp.read_text()), rep2, wait_s=0, state_path=sp)
    created = [c[0] for c in fake.calls[n:] if c[0].endswith("create")]
    assert created == []  # nothing created twice
    assert any(e["stripe"]["op"] for e in audit.query(kind="stripe.call"))


def test_bootstrap_reports_blocked_issuing_with_setup_text(tmp_path):
    b = load_script()
    gw, _, _, _ = gw_with({"v2_request": v2_handler(open_fa=False)})
    rep = b.Report()
    b.bootstrap(gw, {}, rep, wait_s=0, state_path=tmp_path / "s.json")
    assert rep.blocked
    rows = {s: (st, d) for s, st, d in rep.rows}
    st, detail = rows["issuing cardholder + card"]
    assert st == "blocked" and "OPEN v2 financial account" in detail
    assert rows["fund platform balance"][0] == "ok"
    assert rows["connect account (merchant)"][0] == "ok"


def test_bootstrap_inactive_capability_is_blocked_not_ok(tmp_path):
    b = load_script()
    gw, _, _, _ = gw_with({"v2_request": v2_handler(caps="restricted", due=["id_number"])})
    rep = b.Report()
    b.bootstrap(gw, {}, rep, wait_s=0, state_path=tmp_path / "s.json")
    row = next(r for r in rep.rows if r[0] == "connect account (merchant)")
    assert row[1] == "blocked" and "id_number" in row[2]
