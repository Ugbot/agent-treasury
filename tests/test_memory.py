from treasury.memory import CogneeMemory, UnconfiguredMemory
from treasury.policies import PolicyRegistry
from treasury.policy import Clause


class FakeMemory:
    def readiness(self):
        from treasury.memory import Readiness
        return Readiness(True, "fake", "ok")

    def write_policy(self, policy_id, version, content_hash, text):
        from treasury.memory import MemoryWrite
        assert policy_id in text and f"v{version}" in text
        return MemoryWrite(True, "written", "ok", f"fake://{policy_id}/{version}")


C = (Clause("A-1", "text", "require_input", {"key": "k"}),)


def test_unconfigured_never_claims_success():
    m = UnconfiguredMemory()
    assert m.readiness().ready is False
    w = m.write_policy("p", 1, "h", "t")
    assert not w.ok and w.status == "unavailable" and w.source_ref is None and "COGNEE_API_KEY" in w.message


def test_cognee_without_credentials_unavailable(monkeypatch):
    monkeypatch.delenv("COGNEE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    m = CogneeMemory()
    assert m.readiness().ready is False
    assert m.write_policy("p", 1, "h", "t").ok is False


def test_cognee_write_failure_is_reported(monkeypatch):
    monkeypatch.setattr(CogneeMemory, "_installed", staticmethod(lambda: True))
    monkeypatch.setenv("COGNEE_API_KEY", "x")
    w = CogneeMemory().write_policy("p", 1, "h", "t")  # cognee not importable here or fails
    assert w.ok is False and w.source_ref is None


def test_versioned_policy_roundtrip():
    reg = PolicyRegistry(FakeMemory())
    b = reg.publish("p", "T", C)
    assert b.memory.ok and b.memory.source_ref == "fake://p/1"
    assert reg.get("p", 1) is b
    assert b.to_dict()["memory"]["source_ref"] == "fake://p/1"
    # default registry records an honest unavailable write
    b2 = PolicyRegistry().publish("p", "T", C)
    assert b2.memory.ok is False and b2.memory.source_ref is None


# ---- Cognee adapter with a fake client + audit log
import os
import sqlite3

import pytest

from treasury.audit import AuditLog
from treasury.memory import CogneeMemory as CM


class FakeClient:
    def __init__(self, recall_result=None, fail=False):
        self.store, self.recall_result, self.fail = [], recall_result, fail

    async def health(self):
        if self.fail:
            raise ConnectionError("down")
        return True

    async def remember(self, text, dataset):
        if self.fail:
            raise ConnectionError("down")
        self.store.append((dataset, text))

    async def recall(self, query, dataset):
        if self.fail:
            raise ConnectionError("down")
        return self.recall_result if self.recall_result is not None else [t for _, t in self.store]


def _audit():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    return AuditLog(db)


def _decide(a, mission="m1"):
    return a.append("system", "decision", "hold payment", mission_id=mission,
                    payload={"receipt": False},
                    policy={"id": "delivery", "version": 2, "hash": "abcd1234ef", "verdict": "hold",
                            "clauses": [{"id": "RECEIPT-1", "verdict": "fail", "evidence": "no receipt"}]},
                    stripe={"ids": ["pi_123"], "livemode": False})


def test_write_policy_calls_cognee_and_audits():
    a, c = _audit(), FakeClient()
    m = CM(client=c, audit=a)
    w = m.write_policy("delivery", 2, "abcd1234ef", "RECEIPT-1 pay on receipt")
    assert w.ok and w.source_ref == "cognee://agent_treasury/delivery/v2#abcd1234ef"
    assert "delivery v2" in c.store[0][1] and "RECEIPT-1" in c.store[0][1]
    e = a.query(kind="memory.write", actor="cognee")[0]
    assert e["payload"]["status"] == "written"
    st = m.status()
    assert st["configured"] and st["last_write"]["status"] == "written"


def test_failed_write_is_error_and_audited():
    a = _audit()
    m = CM(client=FakeClient(fail=True), audit=a)
    w = m.write_policy("p", 1, "h", "t")
    assert not w.ok and w.status == "error" and w.source_ref is None
    assert a.query(kind="memory.write")[0]["payload"]["status"] == "error"
    assert m.status(probe=True)["reachable"] is False


def test_status_probe_reachable():
    m = CM(client=FakeClient(), audit=_audit())
    assert m.status()["reachable"] is None
    assert m.status(probe=True)["reachable"] is True


def test_sync_decisions_idempotent():
    a, c = _audit(), FakeClient()
    _decide(a)
    a.append("system", "input.event", "noise", mission_id="m1")
    m = CM(client=c, audit=a)
    assert m.sync_decisions()["written"] == 1
    r = m.sync_decisions()
    assert r["written"] == 0 and r["skipped"] == 1 and len(c.store) == 1
    assert "pi_123" in c.store[0][1] and "RECEIPT-1" in c.store[0][1]


def test_explain_from_cognee_cites_clause_and_version():
    a, c = _audit(), FakeClient()
    _decide(a)
    m = CM(client=c, audit=a)
    m.sync_decisions()
    ex = m.explain("m1", "why held?")
    assert ex.source == "cognee"
    assert "RECEIPT-1" in ex.clause_ids
    assert ex.policy_version == {"id": "delivery", "version": 2, "hash": "abcd1234ef"}
    assert ex.evidence and ex.audit_seqs == [1]
    r = a.query(kind="memory.read")[0]
    assert r["actor"] == "cognee" and r["payload"]["status"] == "ok"


def test_explain_empty_cognee_falls_back_honestly():
    a = _audit()
    _decide(a)
    ex = CM(client=FakeClient(recall_result=[]), audit=a).explain("m1", "why?")
    assert ex.source == "audit_log" and "not Cognee" in ex.note
    assert ex.clause_ids == ["RECEIPT-1"] and ex.policy_version["version"] == 2
    assert a.query(kind="memory.read")[0]["payload"]["status"] == "empty"


def test_explain_cognee_down_falls_back():
    a = _audit()
    _decide(a)
    ex = CM(client=FakeClient(fail=True), audit=a).explain("m1", "why?")
    assert ex.source == "audit_log" and "Cognee read failed" in ex.note
    assert a.query(kind="memory.read")[0]["payload"]["status"] == "error"


def test_explain_unconfigured_records_unavailable(monkeypatch):
    for k in ("COGNEE_API_KEY", "COGNEE_SERVICE_URL", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    a = _audit()
    _decide(a)
    m = CM(audit=a)
    ex = m.explain("m1", "why?")
    assert ex.source == "audit_log" and ex.clause_ids == ["RECEIPT-1"]
    assert a.query(kind="memory.read")[0]["payload"]["status"] == "unavailable"
    assert m.sync_decisions()["status"] == "unavailable"
    assert m.status()["configured"] is False
    assert a.verify()["ok"]


def test_api_key_alone_is_not_configured(monkeypatch):
    monkeypatch.setenv("COGNEE_API_KEY", "x")
    monkeypatch.delenv("COGNEE_SERVICE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    from treasury.memory import cognee_mode
    assert cognee_mode() == "none"


@pytest.mark.skipif(not (os.environ.get("COGNEE_LIVE") and (os.environ.get("COGNEE_API_KEY") or os.environ.get("LLM_API_KEY"))),
                    reason="set COGNEE_LIVE=1 plus Cognee credentials to run against real Cognee")
def test_live_cognee_roundtrip():
    a = _audit()
    m = CM(audit=a, dataset="agent_treasury_livetest")
    w = m.write_policy("live-check", 1, "deadbeef", "Clause LIVE-1: payments need a receipt.")
    assert w.ok, w.message
    ex = m.explain("live", "What does clause LIVE-1 require?")
    assert ex.source == "cognee"
