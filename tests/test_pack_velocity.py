import io
import json
import threading
import zipfile

from fastapi.testclient import TestClient

from treasury import routes_policies
from treasury.api import create_app
from treasury.decision import server_facts
from treasury.ledger import Ledger
from treasury.policies import default_registry
from treasury.policy import Clause


def _app(tmp_path, clauses=None, attach=True):
    led = Ledger(tmp_path / "t.sqlite")
    reg = default_registry()
    if clauses:
        reg.publish("org-extra", "Extra", clauses)
        reg.attach("organisation", "org", "org-extra", 1, reg.target_version("organisation", "org"), mandatory=True)
    routes_policies.set_registry(reg)
    try:
        app = create_app(led, use_workflows=False)
    finally:
        pass
    return TestClient(app), led


def _release(c, mid):
    c.post("/missions", json={"id": mid, "amount_minor": 1000, "recipient": "driver_ann"})
    ev = {"event_id": f"e-{mid}", "expected_revision": 0, "location": "destination", "receipt": {}}
    return c.post(f"/missions/{mid}/events", json=ev).json()["verdict"]


def test_server_facts_now_is_float_and_boundary_counted():
    f = server_facts(None, "m", "ann", now=100.5)
    assert f["now"] == 100.5 and f["category_required"] is False
    from treasury.policy import evaluate
    c = Clause("VEL-1", "x", "velocity_limit", {"max_payments": 1, "window_seconds": 60})
    r = evaluate(__import__("treasury.policy", fromlist=["Policy"]).Policy("p", 1, (c,)),
                 {"now": 100.5, "payment_times": [100.5]}, 0, 1)
    assert r.verdict == "hold"


def test_velocity_atomic_under_threads(tmp_path):
    vel = [Clause("VEL-1", "x", "velocity_limit", {"max_payments": 2, "window_seconds": 600})]
    c, led = _app(tmp_path, vel)
    for i in range(8):
        c.post("/missions", json={"id": f"m{i}", "amount_minor": 1000, "recipient": "driver_ann"})
    out = []

    def go(i):
        ev = {"event_id": f"e{i}", "expected_revision": 0, "location": "destination", "receipt": {}}
        out.append(c.post(f"/missions/m{i}/events", json=ev).json()["verdict"])
    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert led.db.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 2


def test_recipient_only_clause_pays_and_audit_has_party(tmp_path):
    cp = [Clause("PAYEE-1", "x", "allowed_counterparties", {"recipients": ["driver_ann"], "categories": ["fuel"]})]
    c, led = _app(tmp_path, cp)
    assert _release(c, "m1") == "allow"
    rows = led.audit.query(mission_id="m1", limit=1000)
    pay = [e for e in rows if e["kind"] == "payment"]
    dec = [e for e in rows if e["kind"] == "decision"]
    assert pay and all(e["party"] == "driver_ann" for e in pay)
    assert dec and all(e["party"] == "sender" for e in dec)


def test_template_is_recipients_only():
    from treasury.jobs import TEMPLATES
    cl = TEMPLATES["tpl-approved-payees"]["clauses"][0]
    assert "categories" not in cl.params


def test_pack_evidence_kinds_stripe_regex_and_resolver(tmp_path):
    c, led = _app(tmp_path)
    log = led.audit
    log.append("agent", "agent.verdict", "approved", mission_id="s1", party="sender", mode="stripe_test",
               policy={"id": "org", "version": 1, "hash": "h", "verdict": "allow", "clauses": []},
               payload={"executed": True, "note": "transfer tr_abc123 done"})
    log.append("system", "settle.pay", "leg paid", mission_id="s1", mode="mock", payload={"op": "settle:s1:driver"})
    log.append("approver", "policy.freeze", "frozen", mission_id="org", party="approver", payload={"active": True})
    r = c.get("/audit/pack", params={"mission": "s1"}, headers={"X-Party-Role": "admin"})
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    dec = json.loads(z.read("decisions.json"))
    assert {d["kind"] for d in dec} == {"agent.verdict", "settle.pay"}
    assert "tr_abc123" in z.read("report.html").decode()
    assert "policy.freeze" in z.read("audit.ndjson").decode()
    assert json.loads(z.read("policies.json"))
    # payment id resolves to its mission
    r2 = c.get("/audit/pack", params={"mission": "pay:s1"}, headers={"X-Party-Role": "admin"})
    assert r2.status_code == 200
