from treasury.audit import AuditLog
from treasury.ledger import Ledger


def test_chain_verifies_and_detects_tampering(tmp_path):
    led = Ledger(tmp_path / "t.sqlite")
    log = AuditLog(led.db)
    log.append("system", "input.event", "receipt", mission_id="m1", party="receiver", payload={"k": "sk_test_ABCDEFGH1234"})
    log.append("agent", "decision", "hold", mission_id="m1", policy={"id": "org-base", "version": 1, "verdict": "hold"})
    assert log.verify()["ok"] and log.verify()["entries"] == 2
    e = log.query(mission_id="m1")
    assert "sk_test_" not in str(e) and e[0]["party"] == "receiver"
    assert [x["seq"] for x in log.query(after=e[0]["seq"])] == [e[1]["seq"]]
    led.db.execute("UPDATE audit_log SET summary='changed' WHERE seq=1")
    assert log.verify() == {"ok": False, "broken_at_seq": 1, "entries": 0}
