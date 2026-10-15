from fastapi.testclient import TestClient

from treasury.api import create_app
from treasury.ledger import Ledger


def test_hold_then_pay_once(tmp_path):
    led = Ledger(tmp_path / "t.sqlite")
    c = TestClient(create_app(led))
    assert c.post("/missions", json={"id": "m", "amount_minor": 1000}).json()["verdict"] == "hold"
    ev = {"event_id": "e1", "expected_revision": 0, "inputs": {"receipt_confirmed": True}}
    assert c.post("/missions/m/events", json=ev).json()["verdict"] == "allow"
    assert c.post("/missions/m/events", json=ev).json()["verdict"] == "allow"  # duplicate
    assert led.db.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
    stale = {**ev, "event_id": "e2"}
    assert c.post("/missions/m/events", json=stale).status_code == 409
