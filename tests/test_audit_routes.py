import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from treasury.audit import AuditLog
from treasury.ledger import Ledger
from treasury.routes_audit import get_audit, router


@pytest.fixture
def env(tmp_path):
    led = Ledger(tmp_path / "t.sqlite")
    app = FastAPI()
    app.state.ledger = led
    app.include_router(router)
    log = get_audit(app.state)
    log.append("system", "input.event", "receipt", mission_id="m1", party="receiver",
               payload={"k": "sk_test_ABCDEFGH1234", "_internal": "x", "ok": 1})
    log.append("agent", "decision", "hold", mission_id="m1", party="sender",
               policy={"id": "org", "version": 2, "hash": "h", "verdict": "hold", "clauses": ["A-1"]},
               stripe={"ids": ["pi_1"], "livemode": False, "op": "create", "secret_note": "n"})
    log.append("driver", "delivery.proof", "photo", mission_id="m2", party="receiver")
    return TestClient(app), led, log


def H(role, pid=None):
    h = {"X-Party-Role": role}
    if pid:
        h["X-Party-Id"] = pid
    return h


def test_get_audit_singleton(env):
    c, _led, log = env
    assert get_audit(c.app.state) is log


def test_auth_required(env):
    c, *_ = env
    assert c.get("/audit").status_code == 401
    assert c.get("/audit", headers=H("hacker", "x")).status_code == 403
    assert c.get("/audit", headers=H("sender")).status_code == 401


def test_admin_sees_all_ordered_with_filters(env):
    c, *_ = env
    d = c.get("/audit", headers=H("admin")).json()["data"]
    assert [e["seq"] for e in d["entries"]] == [1, 2, 3] and d["next_after"] == 3
    assert d["entries"][1]["policy"]["clauses"] == ["A-1"]
    def q(**p):
        return [e["seq"] for e in c.get("/audit", params=p, headers=H("admin")).json()["data"]["entries"]]

    assert q(mission="m1") == [1, 2] and q(kind="delivery.proof") == [3] and q(actor="agent") == [2]
    assert q(party="receiver") == [1, 3] and q(after=1, limit=1) == [2]


def test_party_scoping_and_stripping(env):
    c, *_ = env
    d = c.get("/audit", headers=H("receiver", "receiver")).json()["data"]["entries"]
    assert [e["seq"] for e in d] == [1, 3]
    assert "_internal" not in d[0]["payload"] and d[0]["payload"]["ok"] == 1 and "prev_hash" not in d[0]
    s = c.get("/audit/party/sender", headers=H("sender", "sender")).json()["data"]["entries"]
    assert s[0]["policy"] == {"id": "org", "version": 2, "verdict": "hold"}
    assert "secret_note" not in s[0]["stripe"] and s[0]["stripe"]["ids"] == ["pi_1"]
    # cannot read others
    assert c.get("/audit/party/sender", headers=H("receiver", "receiver")).status_code == 403
    assert c.get("/audit", params={"party": "sender"}, headers=H("receiver", "receiver")).status_code == 403
    assert c.get("/audit/export", params={"party": "sender"}, headers=H("driver", "driver")).status_code == 403
    assert c.get("/audit/party/receiver", headers=H("admin")).json()["data"]["entries"][0]["seq"] == 1


def test_export_ndjson(env):
    c, *_ = env
    r = c.get("/audit/export", headers=H("admin"))
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(x) for x in r.text.strip().split("\n")]
    assert [x["seq"] for x in lines] == [1, 2, 3]
    r2 = c.get("/audit/export", headers=H("receiver", "receiver"))
    assert [json.loads(x)["seq"] for x in r2.text.strip().split("\n")] == [1, 3]


def test_verify_detects_tampering(env):
    c, led, _ = env
    assert c.get("/audit/verify", headers=H("admin")).json()["data"]["ok"] is True
    assert "head" not in c.get("/audit/verify", headers=H("sender", "sender")).json()["data"]
    led.db.execute("UPDATE audit_log SET summary='changed' WHERE seq=2")
    r = c.get("/audit/verify", headers=H("admin")).json()["data"]
    assert r["ok"] is False and r["broken_at_seq"] == 2


def _events(text):
    out = []
    for block in text.split("\n\n"):
        if "event: audit" in block:
            ident = next(ln for ln in block.split("\n") if ln.startswith("id: "))[4:]
            out.append(int(ident))
    return out


def test_stream_ordered_resume_and_scoped(env):
    c, *_ = env
    r = c.get("/audit/stream", params={"follow": "false"}, headers=H("admin"))
    assert r.headers["content-type"].startswith("text/event-stream")
    assert _events(r.text) == [1, 2, 3]
    assert _events(c.get("/audit/stream", params={"follow": "false", "after": 1}, headers=H("admin")).text) == [2, 3]
    h = {**H("admin"), "Last-Event-ID": "2"}
    assert _events(c.get("/audit/stream", params={"follow": "false"}, headers=h).text) == [3]
    rec = c.get("/audit/stream", params={"follow": "false"}, headers=H("receiver", "receiver"))
    assert _events(rec.text) == [1, 3]
    assert c.get("/audit/stream", params={"party": "sender"}, headers=H("receiver", "receiver")).status_code == 403


def test_no_secrets_anywhere(env):
    c, *_ = env
    for path in ("/audit", "/audit/export", "/audit/stream?follow=false"):
        assert "sk_test_" not in c.get(path, headers=H("admin")).text


def test_missing_audit_state():
    app = FastAPI()
    app.include_router(router)
    with pytest.raises(LookupError):
        get_audit(app.state)
    assert isinstance(AuditLog, type)
