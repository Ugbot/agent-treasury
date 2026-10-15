import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from treasury import workflows as wf
from treasury.jobs import JobService
from treasury.ledger import Ledger
from treasury.policies import default_registry
from treasury.routes_jobs import router
from treasury.routes_policies import set_registry

JOB = {"pickup": "Depot", "dropoff": "Customer", "goods_value_minor": 20000, "price_minor": 2000,
       "delivery_fee_minor": 500, "deadline": "2030-01-01T12:00:00Z", "insurance_required": False}


@pytest.fixture
def env(tmp_path):
    led = Ledger(tmp_path / "t.sqlite")
    reg = default_registry()
    set_registry(reg)
    calls = []
    app = FastAPI()
    app.state.ledger = led
    app.state.audit = led.audit
    app.state.jobs = JobService(led, lambda: reg,
                                start_workflow=lambda m: calls.append(m) or {"started": True, "workflow_id": f"mission:{m}"})
    app.include_router(router)
    yield TestClient(app), led, reg, calls
    set_registry(None)


def mk(c, **kw):
    r = c.post("/jobs", json={**JOB, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_validates_and_is_stable(env):
    c, *_ = env
    a, b = mk(c), mk(c)
    assert a["job_id"] == b["job_id"] and a["currency"] == "usd" and a["total_minor"] == 2500
    assert c.get(f"/jobs/{a['job_id']}").json()["pickup"] == "Depot"
    assert c.post("/jobs", json={**JOB, "price_minor": 12.5}).status_code == 422
    assert c.post("/jobs", json={**JOB, "price_minor": 0}).status_code == 422
    assert c.post("/jobs", json={**JOB, "pickup": " "}).status_code == 422
    r = c.post("/jobs", json={**JOB, "currency": "eur"})
    assert r.status_code == 422 and "CURRENCY-1" in r.json()["detail"]
    assert c.get("/jobs/nope").status_code == 404


def test_currency_follows_effective_policy(tmp_path):
    from treasury.policies import PolicyRegistry
    from treasury.policy import Clause
    reg = PolicyRegistry()
    reg.publish("org-eur", "EUR org", [Clause("CURRENCY-1", "eur only", "allowed_currency", {"currencies": ["eur"]})])
    reg.attach("organisation", "org", "org-eur", 1, 0, mandatory=True)
    svc = JobService(Ledger(tmp_path / "c.sqlite"), lambda: reg)
    from treasury.jobs import JobError, JobIn
    assert svc.create(JobIn(**JOB))["currency"] == "eur"
    with pytest.raises(JobError):
        svc.create(JobIn(**JOB, currency="usd"))


def test_library_and_attach_with_locking(env):
    c, *_ = env
    lib = c.get("/jobs/policy-library").json()
    titles = {x["title"] for x in lib}
    assert {"Fuel and meal allowance", "Insurance required", "Refund before pickup"} <= titles
    org = next(x for x in lib if x["policy_id"] == "org-base")
    assert org["mandatory"] and org["locked"]
    j = mk(c)["job_id"]
    eff = c.get(f"/jobs/{j}/policies").json()
    assert [r["policy_id"] for r in eff["rules"]] == ["org-base"] and eff["rules"][0]["locked"]
    r = c.post(f"/jobs/{j}/policies", json={"policy_id": "tpl-fuel-meal", "expected_version": 0})
    assert r.status_code == 200 and len(r.json()["rules"]) == 2
    assert c.post(f"/jobs/{j}/policies", json={"policy_id": "tpl-insurance", "expected_version": 0}).status_code == 409
    assert c.post(f"/jobs/{j}/policies", json={"policy_id": "org-base", "expected_version": 1}).status_code == 400
    r = c.post(f"/jobs/{j}/policies", json={"policy_id": "tpl-approve-above", "expected_version": 1,
                                            "params": {"limit_minor": 1500}})
    assert r.status_code == 200 and any("1500" in str(x["clauses"]) for x in r.json()["rules"])


def test_preview_is_side_effect_free_and_named(env):
    c, led, reg, calls = env
    j = mk(c, insurance_required=True)["job_id"]
    before = led.db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    hist = len(reg.history)
    res = {x["situation"]: x for x in c.post(f"/jobs/{j}/preview").json()["results"]}
    assert led.db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before
    assert len(reg.history) == hist and reg.decisions("job", j) == [] and calls == []
    assert led.mission(f"mission-{j}") is None and led.payments_for(f"mission-{j}") == []
    assert res["happy_path"]["verdict"] == "allow"
    assert (res["receipt_missing"]["verdict"], res["receipt_missing"]["clause"]) == ("hold", "PAY-1")
    assert (res["prohibited_zone"]["verdict"], res["prohibited_zone"]["clause"]) == ("deny", "TRAVEL-1")
    assert (res["over_limit"]["verdict"], res["over_limit"]["clause"]) == ("hold", "APPROVE-1")
    assert (res["insurance_lapsed"]["verdict"], res["insurance_lapsed"]["clause"]) == ("hold", "INSURE-1")
    assert "prohibited zone" in res["prohibited_zone"]["reason"]
    assert c.post(f"/jobs/{j}/preview", json={"situations": ["bogus"]}).status_code == 422


def test_insurance_required_auto_attached_and_not_detachable(env):
    c, *_ = env
    j = mk(c, insurance_required=True)["job_id"]
    rules = c.get(f"/jobs/{j}/policies").json()
    ins = next(r for r in rules["rules"] if r["policy_id"] == "tpl-insurance")
    r = c.delete(f"/jobs/{j}/policies/{ins['attachment_id']}", params={"expected_version": rules["target_version"]})
    assert r.status_code == 403


def test_dispatch_idempotent_freezes_and_audits(env):
    c, led, reg, calls = env
    j = mk(c)["job_id"]
    c.post(f"/jobs/{j}/policies", json={"policy_id": "tpl-fuel-meal", "expected_version": 0})
    d1 = c.post(f"/jobs/{j}/dispatch").json()
    d2 = c.post(f"/jobs/{j}/dispatch").json()
    assert d1["already_dispatched"] is False and d2["already_dispatched"] is True
    assert calls == [d1["mission_id"]]
    assert {f["policy_id"] for f in d1["frozen"]} == {"org-base", "tpl-fuel-meal"}
    m = led.mission(d1["mission_id"])
    assert m["amount_minor"] == 2500 and m["currency"] == "USD"
    assert '"job_id"' in m["inputs"]
    # frozen: no more attachments
    assert c.post(f"/jobs/{j}/policies", json={"policy_id": "tpl-insurance", "expected_version": 1}).status_code == 409
    kinds = [e["kind"] for e in led.audit.query(mission_id=d1["mission_id"])]
    assert kinds.count("job.dispatched") == 1 and kinds.count("job.workflow_started") == 1
    assert all(e["party"] == "sender" for e in led.audit.query(mission_id=d1["mission_id"], kind="job.dispatched"))
    assert led.audit.verify()["ok"]
    # the mission inherits the job's policies
    from treasury.decision import DecisionEngine
    body = DecisionEngine(led, registry=lambda: reg).evaluate(d1["mission_id"])
    assert any(a[1] == "tpl-fuel-meal" for a in body["attachments"]) or "tpl-fuel-meal" in str(body)


def test_dispatch_without_workflow_runtime_is_explicit(tmp_path):
    led = Ledger(tmp_path / "u.sqlite")
    reg = default_registry()
    svc = JobService(led, lambda: reg)  # default start hook; runtime not configured
    from treasury.jobs import JobIn
    j = svc.create(JobIn(**JOB))
    out = svc.dispatch(j["job_id"])
    assert out["workflow"]["started"] is False and out["workflow"]["unavailable"]
    w = svc.watch(j["job_id"])
    assert any(s["kind"] == "job.workflow_unavailable" for s in w["steps"])


def test_watch_narrates_real_workflow(tmp_path):
    led = Ledger(tmp_path / "w.sqlite")
    reg = default_registry()
    set_registry(reg)
    wf.start_runtime(led, str(tmp_path / "sys.sqlite"), wait_seconds=0.2)
    try:
        app = FastAPI()
        app.state.ledger = led
        app.state.audit = led.audit
        app.include_router(router)
        c = TestClient(app)
        j = mk(c)["job_id"]
        d = c.post(f"/jobs/{j}/dispatch").json()
        assert d["workflow"]["started"] is True
        end = time.time() + 10
        while time.time() < end and not led.db.execute(
                "SELECT 1 FROM decisions WHERE mission_id=?", (d["mission_id"],)).fetchone():
            time.sleep(0.05)
        w = c.get(f"/jobs/{j}/watch").json()
        texts = " ".join(s["text"] for s in w["steps"])
        assert w["status"] == "held" and not w["done"]
        assert "on hold" in texts and "receipt" in texts and w["steps"][0]["simulated"] is True
        # delivery receipt arrives, payment is made once
        rev = led.mission(d["mission_id"])["revision"]
        led.apply_event("r1", d["mission_id"], rev, {"receipt_confirmed": True})
        wf.deliver_event(d["mission_id"], "r1")
        end = time.time() + 10
        while time.time() < end and c.get(f"/jobs/{j}/watch").json()["status"] != "paid":
            time.sleep(0.1)
        w = c.get(f"/jobs/{j}/watch").json()
        assert w["status"] == "paid" and w["done"] and len(led.payments_for(d["mission_id"])) == 1
        assert c.get(f"/jobs/{j}/watch", params={"after": w["last_seq"]}).json()["steps"] == []
    finally:
        wf.stop_runtime()
        set_registry(None)
