from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from treasury import policy_author as pa
from treasury import routes_policies
from treasury.api import create_app
from treasury.ledger import Ledger

APPROVER = {"X-Actor-Role": "approver", "X-Actor-Id": "alice"}


class FakeClient:
    def __init__(self, out):
        self.out, self.calls = out, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="propose_policy", input=self.out)])


GOOD = {"policy_id": "meals", "title": "Meals", "unsupported": [
    {"text": "only on weekdays", "reason": "no time-window clause"}],
    "clauses": [{"id": "MEAL-1", "prose": "Meals over $30 need approval", "kind": "max_amount",
                 "params": {"limit_minor": 3000}},
                {"id": "MEAL-2", "prose": "Receipt required", "kind": "require_input",
                 "params": {"key": "receipt_confirmed"}}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    routes_policies.set_registry(None)
    led = Ledger(tmp_path / "t.sqlite")
    c = TestClient(create_app(led))
    yield c, led
    pa.set_client_factory(None)
    routes_policies.set_registry(None)


def fake(out):
    f = FakeClient(out)
    pa.set_client_factory(lambda: f)
    return f


def audit_kinds(led):
    return [e["kind"] for e in led.audit.query(limit=1000)]


def test_clause_kinds(env):
    c, _ = env
    d = c.get("/author/clause-kinds").json()["data"]
    assert {k["kind"] for k in d["kinds"]} == set(pa.KINDS)
    assert any(i["key"] == "receipt_confirmed" for i in d["inputs"])
    assert c.get("/api/author/clause-kinds").status_code == 200


def test_no_key_unavailable(env):
    c, led = env
    d = c.post("/author/draft", json={"description": "meals under $30"}).json()["data"]
    assert d["available"] is False and d["setup"] and "draft" not in d and d["ai_drafted"] is True
    assert "policy.draft" not in audit_kinds(led)


def test_draft_supported_kinds_and_unsupported_reported(env):
    c, led = env
    f = fake(GOOD)
    d = c.post("/author/draft", json={"description": "meals over $30 need approval, weekdays only"}).json()["data"]
    assert d["available"] and d["model"] == "claude-sonnet-5-5"
    assert {x["kind"] for x in d["draft"]["clauses"]} <= set(pa.KINDS)
    assert d["unsupported"] == GOOD["unsupported"]
    assert f.calls[0]["tools"][0]["input_schema"]["properties"]["clauses"]["items"]["properties"]["kind"]["enum"]
    assert "5000" in f.calls[0]["system"]
    # the agent drafted; nothing was published
    assert "meals" not in {b.policy_id for b in routes_policies.get_registry().list()}
    assert "policy.publish" not in [k for k in audit_kinds(led)][-1:]
    e = led.audit.query(kind="policy.draft", limit=10)[0]
    assert e["actor"] == "agent"


@pytest.mark.parametrize("bad", [
    {**GOOD, "clauses": [{"id": "X-1", "prose": "p", "kind": "magic", "params": {}}]},
    {**GOOD, "clauses": [{"id": "X-1", "prose": "p", "kind": "max_amount", "params": {"limit_minor": 50.5}}]},
    {**GOOD, "clauses": [{"id": "X-1", "prose": "p", "kind": "require_input", "params": {"key": "weather"}}]},
    {**GOOD, "clauses": [GOOD["clauses"][0], GOOD["clauses"][0]]},
    {**GOOD, "clauses": [{"id": "bad id", "prose": "p", "kind": "max_amount", "params": {"limit_minor": 1}}]},
])
def test_bad_model_output_rejected(env, bad):
    c, _ = env
    fake(bad)
    r = c.post("/author/draft", json={"description": "x"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "invalid_model_output"
    assert r.json()["error"]["errors"] and "data" not in r.json()


def test_all_unsupported_has_no_draft(env):
    c, _ = env
    fake({**GOOD, "clauses": [], "unsupported": [{"text": "t", "reason": "r"}]})
    d = c.post("/author/draft", json={"description": "x"}).json()["data"]
    assert d["available"] and d.get("draft") is None and d["unsupported"]


def test_secrets_redacted(env):
    c, led = env
    f = fake(GOOD)
    c.post("/author/draft", json={"description": "use sk_test_ABCDEFGHIJKL for meals"})
    assert "sk_test_ABCDEFGHIJKL" not in f.calls[0]["messages"][0]["content"]
    e = led.audit.query(kind="policy.draft", limit=1)[0]
    assert "sk_test_ABCDEFGHIJKL" not in str(e)
    body = {**GOOD, "ai_drafted": True, "expected_version": 0, "prompt": "key sk_test_ABCDEFGHIJKL", "model": "m"}
    assert c.post("/author/publish", json=body, headers=APPROVER).status_code == 200
    assert "sk_test_ABCDEFGHIJKL" not in str(led.audit.query(kind="policy.authored", limit=5))


def test_validate_weakening_and_bad_params(env):
    c, _ = env
    weak = {"policy_id": "org-base", "title": "t", "clauses": [
        {"id": "CURRENCY-1", "prose": "p", "kind": "allowed_currency", "params": {"currencies": ["usd", "eur"]}},
        {"id": "PAY-1", "prose": "p", "kind": "require_input", "params": {"key": "receipt_confirmed"}},
        {"id": "TRAVEL-1", "prose": "p", "kind": "forbid_input", "params": {"key": "prohibited_zone"}},
        {"id": "APPROVE-1", "prose": "p", "kind": "max_amount", "params": {"limit_minor": 99999}}]}
    d = c.post("/author/validate", json=weak).json()["data"]
    assert d["mandatory"] and not d["valid"] and len(d["weakens"]) == 2
    assert d["diff"]["changed"] and {s["id"] for s in d["sentences"]} >= {"APPROVE-1"}
    assert any("limit raised" in e["message"] for e in d["errors"])
    bad = {"policy_id": "p1", "title": "t", "clauses": [
        {"id": "A-1", "prose": "p", "kind": "max_amount", "params": {"limit_minor": "50"}}]}
    d = c.post("/author/validate", json=bad).json()["data"]
    assert not d["valid"] and d["errors"][0]["clause"] == "A-1"
    ok = c.post("/author/validate", json={**GOOD, "expected_version": 0}).json()["data"]
    assert ok["valid"] and ok["sentences"][0]["text"] == "Hold any payment over $30.00 until a person approves it"
    assert ok["sentences"][0]["verdict_if_unmet"] == "hold" and len(ok["diff"]["added"]) == 2


def test_dry_run_matches_registry_and_persists_nothing(env):
    c, led = env
    reg = routes_policies.get_registry()
    before = (len(audit_kinds(led)), len(reg.list()), len(reg.history), len(reg._decisions))
    d = c.post("/author/dry-run", json={"clauses": GOOD["clauses"]}).json()["data"]["results"]
    assert [r["situation"] for r in d] == list(pa.SITUATIONS)
    by = {r["situation"]: r for r in d}
    assert by["all_in_order"]["verdict"] == "hold" and by["all_in_order"]["clause"] == "MEAL-1"
    assert by["receipt_missing"]["verdict"] == "hold" and by["receipt_missing"]["clause"] in ("PAY-1", "MEAL-2")
    assert by["prohibited_zone"]["verdict"] == "deny" and by["prohibited_zone"]["clause"] == "TRAVEL-1"
    assert by["wrong_currency"]["verdict"] == "deny" and by["wrong_currency"]["clause"] == "CURRENCY-1"
    assert by["over_limit"]["verdict"] == "hold"
    # same decide path: a real registry with the same policies agrees
    from treasury.policies import default_registry
    real = default_registry()
    b = real.publish("meals", "Meals", pa.to_clauses(GOOD["clauses"]), 0)
    real.attach("payment", "x", "meals", b.version, 0)
    inputs = {"receipt_confirmed": True, "prohibited_zone": False, "currency": "usd", "insured": True}
    assert real.decide("payment", "x", inputs, 1, 4200).verdict == by["all_in_order"]["verdict"]
    assert real.decide("payment", "x", inputs, 1, 2000).verdict == "allow"
    assert (len(audit_kinds(led)), len(reg.list()), len(reg.history), len(reg._decisions)) == before
    r = c.post("/author/dry-run", json={"clauses": GOOD["clauses"], "situations": ["nope"]})
    assert r.status_code == 422


def test_publish_requires_expected_version_approver_and_audits(env):
    c, led = env
    body = {**GOOD, "ai_drafted": True, "expected_version": 0, "prompt": "meals", "model": "claude-sonnet-5-5"}
    no_ev = {k: v for k, v in body.items() if k != "expected_version"}
    assert c.post("/author/publish", json=no_ev, headers=APPROVER).status_code == 422
    assert c.post("/author/publish", json=body).status_code == 403  # anonymous client
    r = c.post("/author/publish", json=body, headers=APPROVER)
    assert r.status_code == 200 and r.json()["data"]["version"] == 1 and r.json()["data"]["ai_drafted"]
    assert c.post("/author/publish", json=body, headers=APPROVER).status_code == 409
    kinds = audit_kinds(led)
    assert "policy.publish" in kinds
    e = led.audit.query(kind="policy.authored", limit=5)[0]
    assert e["actor"] == "approver" and e["payload"]["ai_drafted"] is True and e["payload"]["model"]
    assert e["policy"]["ai_drafted"] is True
    assert led.audit.verify()["ok"] if hasattr(led.audit, "verify") else True


def test_publish_weakening_mandatory_is_403(env):
    c, _ = env
    body = {"policy_id": "org-base", "title": "t", "ai_drafted": False, "expected_version": 1, "clauses": [
        {"id": "APPROVE-1", "prose": "p", "kind": "max_amount", "params": {"limit_minor": 5000}}]}
    r = c.post("/author/publish", json=body, headers=APPROVER)
    assert r.status_code == 403 and r.json()["error"]["code"] == "mandatory_weakened"


def test_draft_endpoint_cannot_publish(env):
    c, _ = env
    fake(GOOD)
    c.post("/author/draft", json={"description": "meals", "expected_version": 0}, headers=APPROVER)
    assert c.get("/policies/meals").status_code == 404


def test_validate_diff_is_sentences_and_reports_current_version():
    from fastapi.testclient import TestClient

    from treasury.api import create_app

    c = TestClient(create_app())
    body = {"policy_id": "meal", "title": "Meal", "clauses": [
        {"id": "MEAL-1", "prose": "x", "kind": "max_amount", "params": {"limit_minor": 1200}}]}
    d = c.post("/api/author/validate", json=body, headers={"X-Party-Role": "admin", "X-Party-Id": "t"}).json()["data"]
    assert d["current_version"] == 0
    assert d["diff"]["added"] == ["Hold any payment over $12.00 until a person approves it"]
    assert all(isinstance(x, str) for x in d["diff"]["added"] + d["diff"]["removed"])


def _c(cid="MEAL-1", kind="max_amount", params=None):
    return {"id": cid, "prose": "p", "kind": kind, "params": params or {"limit_minor": 100}}


def test_validate_clauses_model_path_checks():
    msgs = lambda cl: " | ".join(e["message"] for e in pa.validate_clauses(cl))
    assert "id must look like" in msgs([_c("MEAL-1\n")])
    assert "id must look like" in msgs([_c("bad")])
    assert "unknown input key" in msgs([_c(kind="require_input", params={"key": "location"})])
    assert "unknown input key" in msgs([_c(kind="forbid_input", params={"key": "nope"})])
    assert "limit_minor" in msgs([_c(params={"limit_minor": 0})])
    assert "limit_minor" in msgs([_c(params={"limit_minor": 10**9 + 1})])
    assert "at most 50" in msgs([_c(f"A-{i}") for i in range(51)])
    assert "prose" in msgs([{**_c(), "prose": "x" * 501}])
    assert "currencies" in msgs([_c(kind="allowed_currency", params={"currencies": ["usd"] * 11})])
    assert pa.validate_clauses([_c()]) == []


def test_policy_id_trailing_newline_rejected(env):
    c, _ = env
    body = {"policy_id": "meals\n", "title": "M", "clauses": [_c()], "expected_version": 0, "ai_drafted": False}
    assert c.post("/author/publish", json=body, headers=APPROVER).status_code == 422
    assert c.post("/author/validate", json=body).status_code == 422


@pytest.mark.parametrize("bad", ["str", ["x"], None, 5])
def test_non_dict_tool_input_is_502(env, bad):
    c, _ = env
    fake(bad)
    r = c.post("/author/draft", json={"description": "x"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "invalid_model_output"


def test_bad_unsupported_entry_is_502(env):
    c, _ = env
    fake({**GOOD, "unsupported": ["just a string"]})
    assert c.post("/author/draft", json={"description": "x"}).status_code == 502
    fake({"policy_id": "a", "title": "t", "clauses": [], "unsupported": ["s"]})
    assert c.post("/author/draft", json={"description": "x"}).status_code == 502


def test_draft_description_cap(env):
    c, _ = env
    assert c.post("/author/draft", json={"description": "x" * 4001}).status_code == 422


def test_dry_run_reason_in_dollars(env):
    c, _ = env
    r = c.post("/author/dry-run", json={"clauses": [_c(params={"limit_minor": 1200})],
                                        "situations": ["over_limit"]}).json()["data"]["results"][0]
    assert "amount $90.00 is over the limit $" in r["reason"] and "minor" not in r["reason"]


def test_secret_tail_redacted():
    from treasury.audit import redact
    for s in ("sk_test_abc123-def456", "sk_test_abc_123456", "whsec_ab+cd/ef=="):
        assert "[REDACTED]" in redact(s) and s[8:] not in redact(s)


def test_published_policies_survive_restart(env, tmp_path):
    c, led = env
    body = {"policy_id": "meals", "title": "Meals", "clauses": [_c()], "expected_version": 0, "ai_drafted": False}
    assert c.post("/author/publish", json=body, headers=APPROVER).status_code == 200
    body["expected_version"] = 1
    body["clauses"] = [_c(params={"limit_minor": 50})]
    assert c.post("/author/publish", json=body, headers=APPROVER).status_code == 200
    from treasury import bitemporal
    from treasury.policies import default_registry
    fresh = default_registry()
    assert bitemporal.replay_registry(fresh, led.audit) >= 2
    assert [b.version for b in fresh._versions["meals"]] == [1, 2]
    assert fresh.get("meals").clauses[0].params["limit_minor"] == 50
    assert bitemporal.replay_registry(fresh, led.audit) == 0  # idempotent


def test_replay_skips_tampered_bundle(env):
    c, led = env
    body = {"policy_id": "meals", "title": "Meals", "clauses": [_c()], "expected_version": 0, "ai_drafted": False}
    c.post("/author/publish", json=body, headers=APPROVER)
    from treasury import bitemporal
    from treasury.policies import PolicyRegistry
    real = bitemporal.AuditLog.query if hasattr(bitemporal, "AuditLog") else None
    entries = led.audit.query(kind="policy.publish", limit=100)
    for e in entries:
        if e["payload"]["bundle"]["policy_id"] == "meals":
            e["payload"]["bundle"]["clauses"][0]["params"]["limit_minor"] = 999999
    class Fake:
        def query(self, **kw):
            return entries
    fresh = PolicyRegistry()
    bitemporal.replay_registry(fresh, Fake())
    assert "meals" not in fresh._versions
    del real
