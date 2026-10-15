import dataclasses

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from treasury.policies import (
    ConflictError,
    MandatoryError,
    PolicyRegistry,
    TargetLockedError,
    default_registry,
)
from treasury.policy import Clause
from treasury.routes_policies import router, set_registry

OK = {"currency": "usd", "receipt_confirmed": True}
PERMISSIVE = (Clause("LOOSE-1", "Anything goes", "max_amount", {"limit_minor": 10**9}),)


def test_publish_versions_and_hash():
    reg = PolicyRegistry()
    b1 = reg.publish("p", "T", PERMISSIVE)
    b2 = reg.publish("p", "T", PERMISSIVE, expected_version=1)
    assert (b1.version, b2.version) == (1, 2) and b1.content_hash != b2.content_hash and b1.verify()
    with pytest.raises(ConflictError):
        reg.publish("p", "T", PERMISSIVE, expected_version=1)


def test_effective_attachments_are_restrictive():
    reg = default_registry()
    reg.publish("loose", "Loose", PERMISSIVE)
    reg.attach("payment", "pay1", "loose", 1, 0)
    # permissive attachment cannot bypass missing receipt (hold) or a denial
    assert reg.decide("payment", "pay1", {}, 0, 1000).verdict == "hold"
    assert reg.decide("payment", "pay1", {**OK, "prohibited_zone": True}, 1, 1000).verdict == "deny"
    # cannot lift the GBP50 approval threshold
    assert reg.decide("payment", "pay1", OK, 2, 9000).verdict == "hold"
    assert reg.decide("payment", "pay1", OK, 3, 1000).verdict == "allow"
    # optional tightening policy makes it stricter
    reg.publish("tight", "Tight", (Clause("T-1", "max 5", "max_amount", {"limit_minor": 500}),))
    reg.attach("payment", "pay1", "tight", 1, 1)
    assert reg.decide("payment", "pay1", OK, 4, 1000).verdict == "hold"


def test_attach_detach_concurrency_and_mandatory():
    reg = default_registry()
    reg.publish("opt", "Opt", PERMISSIVE)
    a = reg.attach("payment", "p", "opt", 1, 0)
    with pytest.raises(ConflictError):
        reg.attach("payment", "p", "opt", 1, 0)
    with pytest.raises(ConflictError):
        reg.detach(a.attachment_id, 0)
    assert reg.detach(a.attachment_id, 1) == 2
    org = reg.attachments_for("organisation", "org")[0]
    with pytest.raises(MandatoryError):
        reg.detach(org.attachment_id, reg.target_version("organisation", "org"))
    # weakened replacement rejected
    weak = tuple(dataclasses.replace(c, params={"limit_minor": 99999}) if c.kind == "max_amount" else c
                 for c in reg.get("org-base").clauses)
    reg.publish("org-base", "weak", weak, expected_version=1)
    with pytest.raises(MandatoryError):
        reg.attach("organisation", "org", "org-base", 2, reg.target_version("organisation", "org"), mandatory=True)
    # non-pending target locked
    reg.lock_target("payment", "p")
    with pytest.raises(TargetLockedError):
        reg.attach("payment", "p", "opt", 1, 2)


def test_stale_policy_holds_payment():
    reg = default_registry()
    assert reg.decide("payment", "x", OK, 0, 1000).verdict == "allow"
    # org policy gets a stricter v2 published but not yet re-attached: required policy stale -> hold
    reg.publish("org-base", "v2", reg.get("org-base").clauses, expected_version=1)
    d = reg.decide("payment", "x", OK, 1, 1000)
    assert d.verdict == "hold" and any("stale" in r for r in d.reasons)
    reg.attach("organisation", "org", "org-base", 2, reg.target_version("organisation", "org"), mandatory=True)
    assert reg.decide("payment", "x", OK, 2, 1000).verdict == "allow"


def test_unknown_policy_and_no_policy_hold():
    assert PolicyRegistry().decide("payment", "z", OK, 0, 1000).verdict == "hold"
    reg = default_registry()
    reg._attach["att_x"] = dataclasses.replace(reg.attachments_for("organisation", "org")[0],
                                               attachment_id="att_x", policy_id="ghost", mandatory=True)
    d = reg.decide("payment", "z", OK, 0, 1000)
    assert d.verdict == "hold" and any("unknown policy" in r for r in d.reasons)


def test_decision_records_immutable_with_versions():
    reg = default_registry()
    d = reg.decide("payment", "i", OK, 0, 1000)
    assert d.input_revision == 0 and d.effective_policy_hash and d.attachments[0][1:3] == ("org-base", 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.verdict = "deny"  # type: ignore[misc]
    assert reg.decide("payment", "i", OK, 0, 1000) is d  # replay returns same record
    reg.publish("opt", "o", PERMISSIVE)
    reg.attach("payment", "i", "opt", 1, 0)
    d2 = reg.decide("payment", "i", OK, 0, 1000)
    assert d2 is not d and d2.effective_policy_hash != d.effective_policy_hash
    assert len(reg.decisions("payment", "i")) == 2 and reg.decisions("payment", "i")[0] is d


def test_routes():
    set_registry(default_registry())
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    try:
        assert c.get("/policies/memory/status").json()["ready"] is False
        r = c.post("/policies/lim/versions", json={"title": "t", "clauses": [
            {"id": "L-1", "prose": "p", "kind": "max_amount", "params": {"limit_minor": 100}}]})
        assert r.status_code == 201 and r.json()["memory"]["ok"] is False
        assert c.post("/policies/lim/versions", json={"title": "t", "clauses": []}).status_code == 409
        body = {"target_type": "payment", "target_id": "p1", "policy_id": "lim", "policy_version": 1,
                "expected_version": 0}
        assert c.post("/policies/attachments", json=body).status_code == 201
        assert c.post("/policies/attachments", json=body).status_code == 409
        d = c.post("/policies/decide", json={"target_id": "p1", "inputs": {"receipt_confirmed": True},
                                             "input_revision": 0, "amount_minor": 1000}).json()
        assert d["verdict"] == "hold"
        org = c.get("/policies/targets/organisation/org/effective").json()["attachments"][0]
        assert c.delete(f"/policies/attachments/{org['attachment_id']}?expected_version=1").status_code == 403
    finally:
        set_registry(None)


def test_failed_publish_of_new_id_does_not_break_list():
    from treasury.policies import ConflictError, PolicyError, PolicyRegistry
    reg = PolicyRegistry()
    with pytest.raises(ConflictError):
        reg.publish("ghost", "G", [Clause("A-1", "p", "max_amount", {"limit_minor": 1})], 3)
    with pytest.raises(PolicyError):
        reg.publish("ghost2", "G", [], 0)
    assert reg.list() == []
    assert "ghost" not in reg._versions and "ghost2" not in reg._versions
