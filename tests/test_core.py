import pytest

from treasury.ledger import Ledger
from treasury.policy import DEFAULT_POLICY, evaluate


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "t.sqlite")


def test_inputs_control_payment():
    held = evaluate(DEFAULT_POLICY, {"currency": "usd"}, 0, 1000)
    assert held.verdict == "hold" and next(r for r in held.results if r["outcome"] == "hold")["clause"] == "PAY-1"
    assert evaluate(DEFAULT_POLICY, {"currency": "usd", "receipt_confirmed": True}, 1, 1000).verdict == "allow"
    denied = evaluate(DEFAULT_POLICY, {"currency": "usd", "receipt_confirmed": True, "prohibited_zone": True}, 2, 1000)
    assert denied.verdict == "deny"
    assert evaluate(DEFAULT_POLICY, {"currency": "usd", "receipt_confirmed": True}, 1, 9000).verdict == "hold"


def test_event_and_operation_idempotency(ledger):
    ledger.create_mission("m1", 1000, "usd")
    assert ledger.apply_event("e1", "m1", 0, {"currency": "usd", "receipt_confirmed": True}) == 1
    assert ledger.apply_event("e1", "m1", 0, {"currency": "usd", "receipt_confirmed": True}) == 1  # duplicate
    with pytest.raises(ValueError):
        ledger.apply_event("e2", "m1", 0, {})  # stale revision
    assert ledger.pay_once("op1", "m1", 1000, "USD", "mock", "mock_1") is True
    assert ledger.pay_once("op1", "m1", 1000, "USD", "mock", "mock_1") is False


def test_rejects_non_positive_amount(ledger):
    with pytest.raises(ValueError):
        ledger.create_mission("m2", 0, "usd")


def test_currency_is_a_policy_clause():
    ok = {"currency": "usd", "receipt_confirmed": True}
    assert evaluate(DEFAULT_POLICY, ok, 1, 1000).verdict == "allow"
    denied = evaluate(DEFAULT_POLICY, {**ok, "currency": "gbp"}, 1, 1000)
    assert denied.verdict == "deny"
    assert next(r for r in denied.results if r["clause"] == "CURRENCY-1")["evidence"]["allowed"] == ["usd"]
    missing = evaluate(DEFAULT_POLICY, {"receipt_confirmed": True}, 1, 1000)
    assert missing.verdict == "hold"
