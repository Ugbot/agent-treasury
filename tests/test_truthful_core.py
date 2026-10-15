"""Stream A: the payment path is truthful (registry-backed, server-owned facts, sticky deny, real approval)."""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from treasury import routes_policies
from treasury.api import create_app
from treasury.audit import AuditLog
from treasury.ledger import LatchedFactError, Ledger, StaleRevisionError
from treasury.policies import default_registry
from treasury.policy import DEFAULT_POLICY, Clause, evaluate
from treasury.saga import refundable_payment

APPROVER = {"X-Actor-Id": "alice", "X-Actor-Role": "approver"}


@pytest.fixture
def env(tmp_path):
    reg = default_registry()
    routes_policies.set_registry(reg)
    led = Ledger(tmp_path / "t.sqlite")
    c = TestClient(create_app(led))
    yield c, led, reg
    routes_policies.set_registry(None)


class Console:
    def __init__(self, c, mid):
        self.c, self.mid, self.n = c, mid, 0

    def rev(self):
        return self.c.get(f"/api/state?missionId={self.mid}").json()["data"]["revision"]

    def send(self, t, payload=None, headers=None, rev=None):
        self.n += 1
        return self.c.post(f"/api/missions/{self.mid}/events", headers=headers or {}, json={
            "eventId": f"{self.mid}-{self.n}", "expectedRevision": self.rev() if rev is None else rev,
            "type": t, "payload": payload or {}})

    def state(self):
        return self.c.get(f"/api/state?missionId={self.mid}").json()["data"]


def scenario(c, name):
    r = c.post("/api/missions", json={"scenario": name}).json()["data"]
    assert r["accepted"]
    return Console(c, c.get("/api/state").json()["data"]["missionId"])


def paid(led, mid):
    return len(led.payments_for(mid))


# -- (1) effective policy from the registry ---------------------------------

def test_attached_stricter_policy_flips_allowed_payment_to_hold(env):
    c, led, reg = env
    reg.publish("cap", "Tight cap", [Clause("CAP-1", "Cap at USD 5", "max_amount", {"limit_minor": 500})])
    m = scenario(c, "happy-path")
    assert m.send("set_location", {"location": "destination"}).status_code == 200
    # without the attachment this receipt would pay USD 20; attach first
    assert m.send("attach_policy", {"targetType": "mission", "targetId": m.mid, "policyId": "cap",
                                    "policyVersion": 1}).json()["data"]["accepted"]
    assert m.send("confirm_receipt").json()["data"]["accepted"]
    s = m.state()
    assert s["decision"]["verdict"] == "hold" and s["operation"] is None and paid(led, m.mid) == 0
    assert {"policyId": "cap", "version": 1} == {k: v for k, v in
                                                 next(p for p in s["policies"] if p["policyId"] == "cap").items()
                                                 if k in ("policyId", "version")}
    cap = next(x for x in s["decision"]["clauses"] if x["clauseId"] == "CAP-1")
    assert cap["outcome"] == "hold" and s["decision"]["approval"]["required"]


def test_attached_policy_can_deny_and_unattached_mission_still_pays(env):
    c, led, reg = env
    reg.publish("nofly", "No flagged", [Clause("FLAG-1", "No flagged jobs", "forbid_input", {"key": "flagged"})])
    a = scenario(c, "happy-path")
    a.send("attach_policy", {"policyId": "nofly", "policyVersion": 1})
    assert c.post(f"/missions/{a.mid}/events", json={"event_id": "fl", "expected_revision": a.rev(),
                                                      "inputs": {"flagged": True}}).json()["verdict"] == "deny"
    a.send("set_location", {"location": "destination"})
    a.send("confirm_receipt")
    assert a.state()["decision"]["verdict"] == "deny" and paid(led, a.mid) == 0
    b = scenario(c, "happy-path")
    b.send("set_location", {"location": "destination"})
    b.send("confirm_receipt")
    assert b.state()["decision"]["verdict"] == "allow" and paid(led, b.mid) == 1


def test_registry_cache_key_includes_inputs_and_amount():
    reg = default_registry()
    base = {"currency": "usd", "receipt_confirmed": True}
    a = reg.decide("mission", "k", base, 1, 1000)
    assert a.verdict == "allow"
    assert reg.decide("mission", "k", base, 1, 9000).verdict == "hold"  # same revision, bigger amount
    assert reg.decide("mission", "k", {**base, "prohibited_zone": True}, 1, 1000).verdict == "deny"
    assert reg.decide("mission", "k", base, 1, 1000) is a  # identical request is the cached object


# -- (3) strict types and server-owned facts --------------------------------

def test_strict_types_in_policy_evaluation():
    ok = {"currency": "usd"}
    assert evaluate(DEFAULT_POLICY, {**ok, "receipt_confirmed": "false"}, 0, 100).verdict == "hold"
    assert evaluate(DEFAULT_POLICY, {**ok, "receipt_confirmed": 1}, 0, 100).verdict == "hold"
    r = evaluate(DEFAULT_POLICY, {**ok, "receipt_confirmed": "true"}, 0, 100)
    assert next(x for x in r.results if x["clause"] == "PAY-1")["evidence"]["invalid_type"] == "str"
    d = evaluate(DEFAULT_POLICY, {**ok, "receipt_confirmed": True, "prohibited_zone": "false"}, 0, 100)
    assert d.verdict == "deny"  # fail closed: a string cannot clear a forbid clause
    assert evaluate(DEFAULT_POLICY, {**ok, "receipt_confirmed": True, "prohibited_zone": False}, 0, 100).verdict == "allow"
    assert evaluate(DEFAULT_POLICY, {**ok, "receipt_confirmed": True}, 0, True).verdict == "deny"  # bool amount


def test_client_cannot_write_server_owned_facts_or_wrong_types(env):
    c, led, _ = env
    c.post("/missions", json={"id": "x", "amount_minor": 1000})
    for key, val in (("receipt_confirmed", True), ("prohibited_zone", False), ("location", "destination"),
                     ("currency", "gbp")):
        r = c.post("/missions/x/events", json={"event_id": f"e-{key}", "expected_revision": 0, "inputs": {key: val}})
        assert r.status_code == 403, key
    r = c.post("/missions/x/events", json={"event_id": "t1", "expected_revision": 0, "inputs": {"insured": "false"}})
    assert r.status_code == 422
    assert paid(led, "x") == 0 and led.mission("x")["revision"] == 0
    assert led.audit.query(mission_id="x", kind="input.rejected")  # refusals are audited


def test_early_receipt_outside_radius_is_denied_and_replayed_nonce_rejected(env):
    c, led, _ = env
    m = scenario(c, "happy-path")
    r = m.send("confirm_receipt")  # still at the depot
    assert r.status_code == 422 and r.json()["error"]["code"] == "receipt_rejected"
    assert r.json()["error"]["gates"]["within_radius"] is False
    m.send("set_location", {"location": "destination"})
    nonce = c.post(f"/api/missions/{m.mid}/receipt-nonce").json()["data"]
    wrong = m.send("confirm_receipt", {"recipientId": "recipient:other", "receiptNonce": nonce["nonce"]})
    assert wrong.status_code == 422 and not led.mission(m.mid)["inputs"].count("receipt_confirmed")
    nonce = c.post(f"/api/missions/{m.mid}/receipt-nonce").json()["data"]
    good = m.send("confirm_receipt", {"recipientId": nonce["recipientId"], "receiptNonce": nonce["nonce"]})
    assert good.json()["data"]["accepted"] and m.state()["decision"]["verdict"] == "allow"
    replay = m.send("confirm_receipt", {"recipientId": nonce["recipientId"], "receiptNonce": nonce["nonce"]})
    assert replay.status_code == 422  # one-use nonce
    assert paid(led, m.mid) == 1


# -- (4) sticky deny and override ------------------------------------------

def test_deny_is_sticky_and_only_an_approver_override_revives_it(env):
    c, led, _ = env
    m = scenario(c, "prohibited-zone")
    assert m.state()["decision"]["verdict"] == "deny"
    # the client re-sends "no prohibited zone" via every door; none works
    assert c.post(f"/missions/{m.mid}/events", json={"event_id": "cl", "expected_revision": m.rev(),
                                                      "inputs": {"prohibited_zone": False}}).status_code == 403
    with pytest.raises(LatchedFactError):
        led.apply_event("direct", m.mid, m.rev(), {"prohibited_zone": False})
    m.send("set_location", {"location": "destination"})  # leaving the zone does not unlatch it
    assert m.send("confirm_receipt").json()["data"]["accepted"]
    s = m.state()
    assert s["decision"]["verdict"] == "deny" and paid(led, m.mid) == 0
    assert s["inputs"]["prohibitedZone"] is True
    pol = s["decision"]["effectivePolicyHash"]
    body = {"clauseIds": ["TRAVEL-1"], "reason": "Road closure confirmed safe by dispatcher", "policyHash": pol}
    assert m.send("override", body).status_code == 403  # not an approver
    assert m.send("override", {**body, "policyHash": "bogus"}, APPROVER).status_code == 409
    assert m.send("override", {**body, "reason": " "}, APPROVER).status_code == 422
    assert m.send("override", body, APPROVER).json()["data"]["accepted"]
    s = m.state()
    assert s["decision"]["verdict"] == "allow" and paid(led, m.mid) == 1
    trav = next(x for x in s["decision"]["clauses"] if x["clauseId"] == "TRAVEL-1")
    assert "overridden_by" in trav["observed"]
    ov = led.audit.query(mission_id=m.mid, kind="override")
    assert len(ov) == 1 and ov[0]["actor"] == "approver" and ov[0]["party"] == "alice"


def test_sticky_deny_survives_a_cleared_input_until_the_policy_changes(env):
    c, led, reg = env
    reg.publish("nofly", "No flagged", [Clause("FLAG-1", "No flagged jobs", "forbid_input", {"key": "flagged"})])
    m = scenario(c, "happy-path")
    m.send("attach_policy", {"policyId": "nofly", "policyVersion": 1})
    post = lambda ev, v: c.post(f"/missions/{m.mid}/events", json={
        "event_id": ev, "expected_revision": m.rev(), "inputs": {"flagged": v}}).json()
    assert post("f1", True)["verdict"] == "deny"
    out = post("f2", False)  # a later client event "clears" the flag: the deny stays
    assert out["verdict"] == "deny" and out["sticky"]
    assert any(r["clause"] == "STICKY-DENY" for r in out["results"])
    m.send("set_location", {"location": "destination"})
    m.send("confirm_receipt")
    assert paid(led, m.mid) == 0
    # a new effective policy (detaching the stricter attachment) re-evaluates from scratch
    att = reg.attachments_for("mission", m.mid)[0]
    reg.detach(att.attachment_id, reg.target_version("mission", m.mid))
    assert m.state()["decision"]["verdict"] == "allow" and paid(led, m.mid) == 1


def test_currency_deny_cannot_be_overridden(env):
    c, _led, _ = env
    c.post("/missions", json={"id": "gbp", "amount_minor": 500, "currency": "gbp"})
    m = Console(c, "gbp")
    assert m.state()["decision"]["verdict"] == "deny"
    h = m.state()["decision"]["effectivePolicyHash"]
    r = m.send("override", {"clauseIds": ["CURRENCY-1"], "reason": "please", "policyHash": h}, APPROVER)
    assert r.status_code == 403 and m.state()["decision"]["verdict"] == "deny"


# -- (5) atomic events -------------------------------------------------------

def test_concurrent_events_never_lose_a_safety_input(tmp_path):
    led = Ledger(tmp_path / "c.sqlite")
    led.create_mission("c1", 1000, "usd")
    errors, done = [], []

    def worker(i):
        while True:
            rev = led.mission("c1")["revision"]
            try:
                done.append(led.apply_event(f"ev{i}", "c1", rev, {f"k{i}": i}))
                return
            except StaleRevisionError:
                continue
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
                return

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert sorted(done) == list(range(1, 13))
    inputs = json.loads(led.mission("c1")["inputs"])
    assert {f"k{i}" for i in range(12)} <= set(inputs)
    revs = [r[0] for r in led.db.execute("SELECT revision FROM events WHERE mission_id='c1'")]
    assert sorted(revs) == list(range(1, 13))
    # same revision twice: exactly one wins, the other is refused (UNIQUE + CAS), nothing partial
    with pytest.raises(StaleRevisionError):
        led.apply_event("late", "c1", 3, {"zz": 1})
    assert "zz" not in json.loads(led.mission("c1")["inputs"])


def test_failed_event_writes_nothing(tmp_path):
    led = Ledger(tmp_path / "a.sqlite")
    led.create_mission("a1", 1000, "usd")
    led.apply_event("e1", "a1", 0, {"prohibited_zone": True})
    before = led.audit.verify()["entries"]
    with pytest.raises(LatchedFactError):
        led.apply_event("e2", "a1", 1, {"prohibited_zone": False})
    assert led.event_revision("e2") is None and led.mission("a1")["revision"] == 1
    assert led.audit.verify()["entries"] == before


# -- (6) real approval -------------------------------------------------------

def test_approval_is_bound_and_amount_cannot_be_lowered_to_bypass(env):
    c, led, _ = env
    m = scenario(c, "over-limit")  # USD 90.00, APPROVE-1 limit is USD 50.00
    m.send("set_location", {"location": "destination"})
    m.send("confirm_receipt")
    s = m.state()
    assert s["decision"]["verdict"] == "hold" and s["decision"]["approval"]["required"]
    bound = s["decision"]["approval"]["bound"]
    approve = {"amountMinor": bound["amountMinor"], "currency": bound["currency"], "recipient": bound["recipient"],
               "policyHash": bound["policyHash"]}
    # lowering the amount below the limit must not slip past the hold
    low = m.send("set_amount", {"amountMinor": 4000, "currency": "usd"})
    assert low.status_code == 409 and low.json()["error"]["code"] == "amount_immutable"
    assert m.state()["inputs"]["amountMinor"] == 9000 and paid(led, m.mid) == 0
    assert m.send("approve", approve).status_code == 403  # not an approver
    for bad in ({"amountMinor": 4000}, {"currency": "gbp"}, {"recipient": "recipient:evil"}, {"policyHash": "x"}):
        r = m.send("approve", {**approve, **bad}, APPROVER)
        assert r.status_code == 409 and r.json()["error"]["code"] == "approval_mismatch"
    assert m.state()["decision"]["verdict"] == "hold" and paid(led, m.mid) == 0
    assert m.send("approve", approve, APPROVER).json()["data"]["accepted"]
    s = m.state()
    assert s["decision"]["verdict"] == "allow" and paid(led, m.mid) == 1
    assert s["operation"]["amountMinor"] == 9000
    ap = led.audit.query(mission_id=m.mid, kind="approval")
    assert len(ap) == 1 and ap[0]["payload"]["bound"]["amount_minor"] == 9000


def test_approval_goes_stale_when_the_effective_policy_changes(env):
    c, led, reg = env
    reg.publish("extra", "Extra", [Clause("X-1", "need escort", "require_input", {"key": "escort"})])
    m = scenario(c, "over-limit")
    m.send("set_location", {"location": "destination"})
    m.send("confirm_receipt")
    b = m.state()["decision"]["approval"]["bound"]
    m.send("approve", {"amountMinor": b["amountMinor"], "currency": b["currency"], "recipient": b["recipient"],
                       "policyHash": b["policyHash"]}, APPROVER)
    assert paid(led, m.mid) == 1  # approved under the original effective policy
    # a second mission approved, then a stricter policy attached before payment is impossible after payment
    n = scenario(c, "over-limit")
    n.send("set_location", {"location": "destination"})
    nb = n.state()["decision"]["approval"]["bound"]
    n.send("attach_policy", {"policyId": "extra", "policyVersion": 1})
    stale = n.send("approve", {"amountMinor": nb["amountMinor"], "currency": nb["currency"],
                               "recipient": nb["recipient"], "policyHash": nb["policyHash"]}, APPROVER)
    assert stale.status_code == 409  # hash moved: approval of the old policy is refused
    assert paid(led, n.mid) == 0
    locked = m.send("attach_policy", {"policyId": "extra", "policyVersion": 1})
    assert locked.status_code == 409  # money already moved: policies are locked


# -- (7) refunds read real state ---------------------------------------------

def test_refundable_payment_reads_ledger_state(env):
    c, led, _ = env
    assert refundable_payment(led, "nope") is None
    m = scenario(c, "happy-path")
    assert refundable_payment(led, m.mid) is None
    m.send("set_location", {"location": "destination"})
    m.send("confirm_receipt")
    st = refundable_payment(led, m.mid)
    assert st["amount_minor"] == 2000 and st["refundable_minor"] == 2000 and st["state"] == "confirmed"
    assert c.get(f"/api/missions/{m.mid}/payment-state").json()["data"]["operation_id"] == st["operation_id"]


# -- (8) audit ---------------------------------------------------------------

def test_every_step_is_audited_and_chain_verifies(env):
    c, led, _ = env
    m = scenario(c, "over-limit")
    m.send("set_location", {"location": "destination"})
    m.send("confirm_receipt")
    b = m.state()["decision"]["approval"]["bound"]
    m.send("approve", {"amountMinor": b["amountMinor"], "currency": b["currency"], "recipient": b["recipient"],
                       "policyHash": b["policyHash"]}, APPROVER)
    m.state()
    kinds = [e["kind"] for e in AuditLog(led.db).query(mission_id=m.mid)]
    for k in ("mission.created", "input.event", "delivery.proof", "decision", "approval", "payment"):
        assert k in kinds, k
    dec = [e for e in AuditLog(led.db).query(mission_id=m.mid, kind="decision")]
    assert all(e["policy"]["clauses"] and e["policy"]["hash"] for e in dec)
    assert any(e["policy"]["verdict"] == "hold" for e in dec) and dec[-1]["policy"]["verdict"] == "allow"
    assert led.audit.verify()["ok"]
