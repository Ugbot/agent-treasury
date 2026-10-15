from fastapi.testclient import TestClient

from treasury.api import create_app
from treasury.ledger import Ledger


def test_history_routes_mounted_and_valid_at(tmp_path):
    led = Ledger(str(tmp_path / "t.db"))
    app = create_app(led)
    c = TestClient(app)
    mid = "m-hist"
    led.create_mission(mid, 4200, "usd")
    led.apply_event("e1", mid, 0, {"scenario": "x"}, valid_at="2026-10-15T10:00:00+00:00")
    row = next(e for e in led.audit.query(mission_id=mid) if e["kind"] == "input.event")
    assert row["valid_at"] == "2026-10-15T10:00:00+00:00"
    for prefix in ("", "/api"):
        r = c.get(f"{prefix}/history/payment/{mid}")
        assert r.status_code == 200 and r.json()["timeline"]
        r = c.get(f"{prefix}/history/policy-as-of", params={"target_type": "mission", "target_id": mid,
                                                           "at": "2026-10-15T12:00:00+00:00"})
        assert r.status_code == 200
    assert app.state.audit.query(kind="policy.publish") or app.state.audit.query(kind="policy.attach")
