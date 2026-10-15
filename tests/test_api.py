from fastapi.testclient import TestClient

from treasury.api import create_app
from treasury.ledger import Ledger


def test_hold_then_pay_once(tmp_path):
    led = Ledger(tmp_path / "t.sqlite")
    c = TestClient(create_app(led))
    assert c.post("/missions", json={"id": "m", "amount_minor": 1000}).json()["verdict"] == "hold"
    # a client can no longer assert receipt_confirmed: it is a server-owned fact
    forged = {"event_id": "f1", "expected_revision": 0, "inputs": {"receipt_confirmed": True}}
    assert c.post("/missions/m/events", json=forged).status_code == 403
    ev = {"event_id": "e1", "expected_revision": 0, "location": "destination", "receipt": {}}
    assert c.post("/missions/m/events", json=ev).json()["verdict"] == "allow"
    assert c.post("/missions/m/events", json=ev).json()["verdict"] == "allow"  # duplicate
    assert led.db.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
    stale = {**ev, "event_id": "e2"}
    assert c.post("/missions/m/events", json=stale).status_code == 409


def test_routers_mounted_and_console_flow(tmp_path):
    c = TestClient(create_app(Ledger(tmp_path / "t.sqlite")))
    assert c.get("/health").json() == {"ok": True, "mode": "mock", "durable": False}
    assert c.get("/api/policies").status_code == 200
    assert c.get("/delivery/status/none").status_code in (200, 404)
    assert c.get("/api/chat/status").json()["available"] in (True, False)
    assert c.get("/api/scenarios").json()["data"]
    s = c.get("/api/state?missionId=current").json()["data"]
    assert s["decision"]["verdict"] == "hold" and s["operation"] is None
    mid = s["missionId"]
    ev = {"eventId": "c1", "expectedRevision": s["revision"], "type": "confirm_receipt", "payload": {}}
    # depot is outside the destination radius: the server-side proof gate rejects the receipt
    assert c.post(f"/api/missions/{mid}/events", json=ev).json()["error"]["code"] == "receipt_rejected"
    loc = {"eventId": "c0", "expectedRevision": s["revision"], "type": "set_location",
           "payload": {"location": "destination"}}
    assert c.post(f"/api/missions/{mid}/events", json=loc).json()["data"]["accepted"]
    ev["expectedRevision"] = s["revision"] + 1
    assert c.post(f"/api/missions/{mid}/events", json=ev).json()["data"]["accepted"]
    s2 = c.get("/api/state").json()["data"]
    assert s2["decision"]["verdict"] == "allow" and s2["operation"]["mode"] == "mock"
    assert c.post(f"/api/missions/{mid}/events", json={**ev, "eventId": "c2"}).status_code == 409


def test_durable_workflow_path_pays_once(tmp_path, monkeypatch):
    from treasury import workflows
    monkeypatch.setenv("TREASURY_DBOS_DB", str(tmp_path / "dbos.sqlite"))
    led = Ledger(tmp_path / "t.sqlite")
    try:
        c = TestClient(create_app(led, use_workflows=True))
        assert c.post("/missions", json={"id": "w", "amount_minor": 1000}).json()["verdict"] == "hold"
        ev = {"event_id": "e1", "expected_revision": 0, "location": "destination", "receipt": {}}
        r = c.post("/missions/w/events", json=ev).json()
        assert r["verdict"] == "allow" and r["workflow"]["status"] == "paid"
        assert led.db.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
    finally:
        workflows.stop_runtime()
