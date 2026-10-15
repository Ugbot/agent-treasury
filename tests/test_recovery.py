import pytest

from treasury import workflows as wf
from treasury.ledger import Ledger
from treasury.saga import MockGateway, ProviderError, SagaStore


def test_waiting_mission_resumes(tmp_path):
    led = Ledger(tmp_path / "l.sqlite")
    sysdb = str(tmp_path / "sys.sqlite")
    gw = MockGateway()
    wf.start_runtime(led, sysdb, gw, wait_seconds=0.2)
    led.create_mission("r1", 1000, "USD")
    wf.start_mission("r1")
    wf.stop_runtime()  # "crash" while waiting for input
    led.apply_event("e1", "r1", 0, {"receipt_confirmed": True})  # event arrives while down
    wf.start_runtime(led, sysdb, gw, wait_seconds=0.2)  # restart: recovers pending workflow
    try:
        wf.deliver_event("r1", "e1")
        h = wf.start_mission("r1")  # same ID -> same workflow, no second run
        assert h.get_result()["status"] == "paid"
        assert led.db.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
        assert gw.calls.count(("pay", "pay:r1")) == 1
    finally:
        wf.stop_runtime()


class CrashAfterProvider(MockGateway):
    """Provider accepts the payment, then the process 'dies' before checkpoint."""

    def __init__(self):
        super().__init__()
        self.crash = True

    def pay(self, operation_id, amount_minor, currency, key):
        res = super().pay(operation_id, amount_minor, currency, key)
        self.objects[operation_id] = res  # provider-side object visible to reconcile
        if self.crash:
            self.crash = False
            raise ProviderError("connection lost after provider success")
        return res


@pytest.fixture
def store(tmp_path):
    return SagaStore(Ledger(tmp_path / "l.sqlite"))


def test_provider_success_before_checkpoint(store):
    gw = CrashAfterProvider()
    store.ledger.create_mission("p1", 700, "USD")
    store.reserve("pay:p1", "p1", "payment", 700, "USD", "mock")
    with pytest.raises(ProviderError):
        store.execute("pay:p1", gw)
    assert store.get("pay:p1")["state"] == "unknown"
    op = store.execute("pay:p1", gw)  # retry reconciles, does not pay again
    assert op["state"] == "confirmed"
    assert gw.calls.count(("pay", "pay:p1")) == 1
    store.execute("pay:p1", gw)
    assert store.ledger.db.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1


def test_reserve_atomic_and_params_validated(store):
    store.reserve("pay:x", "x", "payment", 100, "usd", "mock")
    assert store.reserved_total("x", "USD") == 100
    store.reserve("pay:x", "x", "payment", 100, "USD", "mock")  # idempotent
    assert store.reserved_total("x", "USD") == 100
    with pytest.raises(ValueError):
        store.reserve("pay:x", "x", "payment", 101, "USD", "mock")
    with pytest.raises(ValueError):
        store.reserve("pay:y", "y", "payment", 0, "USD", "mock")


def test_confirmed_leg_survives_restart_and_compensation_policy(tmp_path):
    led = Ledger(tmp_path / "l.sqlite")
    gw = MockGateway()
    s = SagaStore(led)
    led.create_mission("c1", 300, "USD")
    s.reserve("pay:c1", "c1", "payment", 300, "USD", "mock")
    s.execute("pay:c1", gw)
    s2 = SagaStore(Ledger(tmp_path / "l.sqlite"))  # fresh connection = restart
    assert s2.get("pay:c1")["state"] == "confirmed"
    denied = s2.compensate("pay:c1", gw, policy_permits=False)
    assert denied["state"] == "confirmed" and ("refund", "comp:pay:c1") not in gw.calls
    done = s2.compensate("pay:c1", gw, policy_permits=True)
    assert done["state"] == "compensated"
    s2.compensate("pay:c1", gw, policy_permits=True)
    assert gw.calls.count(("refund", "comp:pay:c1")) == 1


def test_failed_compensation_is_visible(store):
    class BadRefund(MockGateway):
        def refund(self, *a):
            raise ProviderError("down")

    gw = BadRefund()
    store.ledger.create_mission("f1", 300, "USD")
    store.reserve("pay:f1", "f1", "payment", 300, "USD", "mock")
    store.execute("pay:f1", gw)
    op = store.compensate("pay:f1", gw, policy_permits=True, max_attempts=2)
    assert op["state"] == "compensation_required"
    assert op["evidence"][-1]["escalate"] is True
    assert "pay:f1" in [o["operation_id"] for o in store.unresolved()]


def test_reserved_only_compensation_releases_authority(store):
    store.reserve("pay:z", "z", "payment", 50, "USD", "mock")
    op = store.compensate("pay:z", MockGateway(), policy_permits=True)
    assert op["state"] == "compensated" and store.reserved_total("z", "USD") == 0
