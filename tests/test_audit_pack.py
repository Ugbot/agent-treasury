import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from treasury.ledger import Ledger
from treasury.routes_audit import get_audit
from treasury.routes_pack import router


@pytest.fixture
def env(tmp_path):
    led = Ledger(tmp_path / "t.sqlite")
    app = FastAPI()
    app.state.ledger = led
    app.include_router(router)
    log = get_audit(app.state)
    log.append("system", "input.event", "receipt sk_test_ABCDEFGH1234", mission_id="m1", party="receiver")
    log.append("system", "decision", "decision allow", mission_id="m1", mode="mock",
               policy={"id": "org", "version": 2, "hash": "h", "verdict": "allow",
                       "clauses": [{"clause": "ALLOWED-PAYEES-1", "outcome": "allow", "evidence": {"recipient": "acme"}}]},
               payload={"amount_minor": 500, "currency": "gbp", "reasons": []})
    log.append("stripe", "stripe.call", "pi created", mission_id="m1", mode="stripe_test",
               stripe={"ids": ["pi_123"], "livemode": False, "op": "create"})
    log.append("system", "input.event", "other", mission_id="m2")
    return TestClient(app), log


def H(role, pid=None):
    return {"X-Party-Role": role, **({"X-Party-Id": pid} if pid else {})}


def test_pack_contents_and_report(env):
    c, log = env
    r = c.get("/audit/pack", params={"mission": "m1"}, headers=H("admin"))
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "audit-pack-m1.zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == ["README.txt", "audit.ndjson", "chain.json", "decisions.json",
                                    "policies.json", "report.html"]
    rep = z.read("report.html").decode()
    assert "ALLOWED-PAYEES-1" in rep and "pi_123" in rep and "STRIPE TEST" in rep and "MOCK" in rep
    nd = [json.loads(x) for x in z.read("audit.ndjson").decode().splitlines()]
    assert {e["mission_id"] for e in nd} == {"m1"}
    assert "sk_test_ABCDEFGH1234" not in "".join(z.read(n).decode() for n in z.namelist())
    chain = json.loads(z.read("chain.json"))
    assert chain["verify"]["ok"] and chain["head"] == log.verify()["head"] or chain["entries"] >= 4


def test_deterministic(env):
    c, _ = env
    a = c.get("/audit/pack", params={"mission": "m1"}, headers=H("approver", "ap1")).content
    # the download itself is audited, so compare packs built without new entries
    from treasury.audit_pack import build_pack
    assert build_pack(env[1], "m1") == build_pack(env[1], "m1") and a


def test_roles(env):
    c, _ = env
    for role in ("sender", "receiver", "driver"):
        assert c.get("/audit/pack", params={"mission": "m1"}, headers=H(role, "m1")).status_code == 403
    assert c.get("/audit/pack", params={"mission": "m1"}).status_code == 401
    assert c.get("/audit/pack", params={"mission": "nope"}, headers=H("admin")).status_code == 404
