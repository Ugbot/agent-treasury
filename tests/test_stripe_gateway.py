import json

import pytest
import stripe

from treasury.gateway import (
    CapabilityUnavailable,
    GatewayError,
    LiveModeError,
    MockGateway,
    PaymentGateway,
)
from treasury.probe import exit_code, run_probe
from treasury.stripe_gateway import StripeGateway, redact, validate_test_key

KEY = "sk_test_" + "A" * 24


def test_rejects_live_mode(monkeypatch):
    for bad in ("sk_live_" + "x" * 20, "rk_live_" + "x" * 20, "sk_foo"):
        with pytest.raises(LiveModeError) as e:
            validate_test_key(bad)
        assert bad not in str(e.value)
    with pytest.raises(LiveModeError):
        StripeGateway(api_key="sk_live_abc")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_abc")
    with pytest.raises(LiveModeError):
        run_probe()


def test_rejects_livemode_true_object():
    class C:
        class payment_intents:
            @staticmethod
            def create(params, options=None):
                return {"id": "pi_1", "status": "succeeded", "livemode": True, "amount": 5}

    with pytest.raises(LiveModeError):
        StripeGateway(client=C).create_payment("op", 5, "usd")


def test_missing_key_is_unavailable_with_setup(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    with pytest.raises(CapabilityUnavailable) as e:
        StripeGateway()
    assert "sk_test_" in e.value.setup
    rep = run_probe(env={})
    assert rep["configured"] is False
    assert set(rep["capabilities"]) == {"payments", "connect_transfers", "issuing",
                                        "invoicing"}
    for c in rep["capabilities"].values():
        assert c["status"] == "unavailable" and c["setup"]
    assert exit_code(rep) == 2


def test_mock_gateway_conforms_and_idempotent():
    g = MockGateway()
    assert isinstance(g, PaymentGateway)
    p1 = g.create_payment("o1", 1000, "USD")
    assert g.create_payment("o1", 1000, "usd") is p1
    assert (p1.mode, p1.livemode, p1.currency) == ("mock", False, "usd")
    card = g.issue_card("c", "A B")
    auth = g.authorize_fuel("a", card.provider_ref, 500)
    assert auth.approved
    assert g.capture_fuel("k", auth.provider_ref).status == "captured"
    assert g.transfer("t", 10, "usd", "acct_x").destination == "acct_x"
    assert g.create_invoice("i", 10, "usd", "a@b.c").status == "paid"
    assert g.refund("r", p1.provider_ref).payment_ref == p1.provider_ref
    with pytest.raises(ValueError):
        g.create_payment("bad", 10.5, "usd")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        g.transfer("bad2", 0, "usd", "acct_x")


def test_redaction():
    out = redact({"client_secret": "pi_x_secret_y", "msg": f"bad key {KEY}", "n": [KEY]})
    s = json.dumps(out)
    assert KEY not in s and "pi_x_secret_y" not in s


class _Fake:
    """Minimal fake of the stripe client for a fully-provisioned sandbox."""

    def __init__(self, issuing=True, funded=True, accounts=2):
        self.issuing_enabled, self.funded, self.n = issuing, funded, accounts
        self.created = []
        f = self
        ok = lambda **kw: {"livemode": False, **kw}

        class Res:
            def __init__(s, fn):
                s.fn = fn

            def __getattr__(s, name):
                return lambda *a, **k: s.fn(name, *a, **k)

        def ns(**kw):
            class N:
                pass
            for k, v in kw.items():
                setattr(N, k, v)
            return N

        def rec(kind, **base):
            def fn(name, *a, **k):
                f.created.append((kind, name))
                return ok(id=f"{kind}_{len(f.created)}", **base)
            return Res(fn)

        def accounts_fn(name, *a, **k):
            if name == "retrieve":
                return ok(id="acct_plat", country="GB", default_currency="usd")
            return ok(data=[{"id": f"acct_{i}", "country": "GB",
                             "capabilities": {"transfers": "active"}} for i in range(f.n)])

        def gated(kind, **base):
            def fn(name, *a, **k):
                if not f.issuing_enabled:
                    raise stripe.PermissionError("Your account is not enabled for Issuing")
                f.created.append((kind, name))
                extra = {}
                if kind == "auth":
                    extra = {"approved": f.funded,
                             "status": "pending" if f.funded else "closed"}
                if name == "capture":
                    extra = {"status": "closed"}
                return ok(id=f"{kind}_{len(f.created)}", **{**base, **extra})
            return Res(fn)

        self.accounts = Res(accounts_fn)
        self.v2_request = lambda method, path, body=None, preview=False: {
            "data": [{"id": "fa_1", "status": "open"}]}
        self.payment_intents = rec("pi", status="succeeded", amount=100, currency="usd")
        self.refunds = rec("re", status="succeeded", amount=100, currency="usd")
        self.transfers = rec("tr", amount=100, currency="usd", reversed=False)
        self.customers = rec("cus")
        self.invoice_items = rec("ii")
        self.invoices = rec("in", status="paid", amount_paid=100, currency="usd",
                            hosted_invoice_url="https://x/inv")
        self.balance = ns(retrieve=staticmethod(lambda: ok(
            available=[{"currency": "usd", "amount": 9000 if f.funded else 0}],
            pending=[{"currency": "usd", "amount": 0 if f.funded else 9000}],
            issuing={"available": [{"currency": "usd", "amount": 5000 if f.funded else 0}]})))
        self.issuing = ns(cardholders=gated("ich", status="active"),
                          cards=gated("ic", status="active", last4="4242", currency="usd"))
        self.test_helpers = ns(issuing=ns(authorizations=gated(
            "auth", amount=100, currency="usd")))


def test_probe_all_ok_reports_each_capability():
    rep = run_probe(api_key=KEY, client=_Fake(), env={})
    assert {k: v["status"] for k, v in rep["capabilities"].items()} == {
        "payments": "ok", "connect_transfers": "ok", "issuing": "ok", "invoicing": "ok"}
    assert rep["capabilities"]["payments"]["funding"]["available"]["usd"] == 9000
    assert "available" in rep["capabilities"]["issuing"]["funding"]
    assert len(set(rep["capabilities"]["connect_transfers"]["evidence"]["destinations"])) == 2
    assert exit_code(rep) == 0
    assert KEY not in json.dumps(rep)


def test_probe_issuing_unavailable_and_single_connect_account():
    rep = run_probe(api_key=KEY, client=_Fake(issuing=False, accounts=1), env={})
    caps = rep["capabilities"]
    assert caps["issuing"]["status"] == "unavailable" and "Issuing" in caps["issuing"]["setup"]
    assert caps["connect_transfers"]["status"] == "unavailable"
    assert caps["payments"]["status"] == "ok"
    assert exit_code(rep) == 2


def test_pending_funds_not_called_available():
    rep = run_probe(api_key=KEY, client=_Fake(funded=False), env={})
    caps = rep["capabilities"]
    assert caps["connect_transfers"]["status"] == "partial"
    assert caps["issuing"]["status"] == "partial"
    assert caps["connect_transfers"]["funding"]["pending"]["usd"] == 9000


def test_stripe_error_becomes_gateway_error():
    class C:
        class refunds:
            @staticmethod
            def create(params, options=None):
                raise stripe.InvalidRequestError(f"boom {KEY}", "p")

    with pytest.raises(GatewayError) as e:
        StripeGateway(client=C).refund("o", "pi_1")
    assert KEY not in str(e.value)
