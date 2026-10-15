import json
import time

import pytest

from treasury import workflows as wf
from treasury.ledger import Ledger
from treasury.saga import MockGateway


@pytest.fixture
def env(tmp_path):
    led = Ledger(tmp_path / "l.sqlite")
    gw = MockGateway()
    wf.start_runtime(led, str(tmp_path / "sys.sqlite"), gw, wait_seconds=0.2)
    yield led, gw
    wf.stop_runtime()


def send(led, mission, event_id, patch):
    rev = led.mission(mission)["revision"]
    led.apply_event(event_id, mission, rev, patch)
    wf.deliver_event(mission, event_id)


def wait_for_decision(led, mission, revision, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if led.db.execute("SELECT 1 FROM decisions WHERE mission_id=? AND revision=?", (mission, revision)).fetchone():
            return
        time.sleep(0.05)
    raise AssertionError("workflow never decided")


def test_hold_then_allow_pays_once(env):
    led, gw = env
    led.create_mission("m1", 1000, "USD")
    h = wf.start_mission("m1")
    assert wf.start_mission("m1").get_workflow_id() == h.get_workflow_id()  # duplicate start
    send(led, "m1", "e1", {"receipt_confirmed": True})
    out = h.get_result()
    assert out["status"] == "paid" and out["payment"]["mode"] == "mock"
    assert out["decision"]["policy_version"] == 1
    assert {r["clause"] for r in out["decision"]["results"]} >= {"CURRENCY-1", "PAY-1"}
    assert led.db.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
    assert gw.calls.count(("pay", "pay:m1")) == 1


def test_hold_decision_has_evidence_and_deny(env):
    led, _ = env
    led.create_mission("m2", 1000, "USD")
    h = wf.start_mission("m2")
    wait_for_decision(led, "m2", 0)
    send(led, "m2", "e1", {"prohibited_zone": True})
    out = h.get_result()
    assert out["status"] == "denied"
    d = json.loads(led.db.execute("SELECT body FROM decisions WHERE mission_id='m2' ORDER BY revision DESC").fetchone()[0])
    assert d["verdict"] == "deny"
    assert any(r["clause"] == "TRAVEL-1" and r["evidence"] for r in d["results"])
    first = json.loads(led.db.execute("SELECT body FROM decisions WHERE mission_id='m2' AND revision=0").fetchone()[0])
    assert first["verdict"] == "hold" and next(r for r in first["results"] if r["clause"] == "PAY-1")["evidence"] == {"missing": "receipt_confirmed"}


def test_duplicate_event_delivery_single_effect(env):
    led, _ = env
    led.create_mission("m3", 500, "USD")
    h = wf.start_mission("m3")
    send(led, "m3", "e1", {"receipt_confirmed": True})
    wf.deliver_event("m3", "e1")
    assert h.get_result()["status"] == "paid"
    assert led.db.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1


def test_unavailable_gateway_is_not_success(tmp_path):
    from treasury.saga import GatewayUnavailable

    class Down(MockGateway):
        mode = "mock"

        def pay(self, *a):
            raise GatewayUnavailable("set STRIPE_TEST_KEY to a test-mode key")

    led = Ledger(tmp_path / "l.sqlite")
    wf.start_runtime(led, str(tmp_path / "s.sqlite"), Down(), wait_seconds=0.2)
    try:
        led.create_mission("m4", 500, "USD")
        led.apply_event("e1", "m4", 0, {"receipt_confirmed": True})
        out = wf.start_mission("m4").get_result()
        assert out["status"] == "payment_unavailable" or out["status"] == "payment_failed"
        assert led.db.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
    finally:
        wf.stop_runtime()
