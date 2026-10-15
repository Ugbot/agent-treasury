import sqlite3

import pytest

from treasury.audit import AuditLog
from treasury.bitemporal import (
    PolicyTimeline,
    instrument_registry,
    payment_history,
    reevaluate,
    reevaluate_recorded,
)
from treasury.ledger import Ledger
from treasury.policies import PolicyRegistry
from treasury.policy import DEFAULT_POLICY, Clause

T = lambda h, m=0: f"2026-10-15T{h:02d}:{m:02d}:00+00:00"


def clauses(limit):
    return tuple(Clause(c.id, c.prose, c.kind, {"limit_minor": limit} if c.kind == "max_amount" else c.params)
                 for c in DEFAULT_POLICY.clauses)


class Env:
    def __init__(self, tmp_path):
        self.led = Ledger(tmp_path / "t.sqlite")
        self.audit = AuditLog(self.led.db)
        self.reg = PolicyRegistry()
        self.now = [T(8)]
        instrument_registry(self.reg, self.audit, clock=lambda: self.now[0])
        self.tl = PolicyTimeline(self.audit)

    def at(self, t):
        self.now[0] = t

    def publish_attach(self, t, limit, version):
        self.at(t)
        self.reg.publish("org-base", "Organisation base policy", clauses(limit), expected_version=version - 1)
        self.reg.attach("organisation", "org", "org-base", version, 0 if version == 1 else version - 1, mandatory=True)

    def decide(self, mid, t, amount, rev=1, extra=None):
        inputs = {"currency": "usd", "receipt_confirmed": True, **(extra or {})}
        eff = self.reg.decide("mission", mid, inputs, rev, amount, [])
        clauses_ = [dict(r) for r in eff.results]
        return self.audit.append(
            "system", "decision", f"decision {eff.verdict}", mission_id=mid, valid_at=t,
            policy={"id": "org-base", "version": eff.attachments[0][2], "hash": eff.effective_policy_hash,
                    "verdict": eff.verdict, "clauses": clauses_},
            payload={"revision": rev, "facts": {"receipt_confirmed": True, **(extra or {})}, "currency": "usd",
                     "attachments": [list(a) for a in eff.attachments], "amount_minor": amount})


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def versions(r):
    return [(a["policy_id"], a["version"]) for a in r["attachments"]]


def test_policy_as_of_v1_before_v2_and_v2_after(env):
    env.publish_attach(T(9), 5000, 1)
    env.publish_attach(T(12), 3000, 2)
    before = env.tl.policy_as_of("mission", "m1", T(10), "valid")
    after = env.tl.policy_as_of("mission", "m1", T(13), "valid")
    assert versions(before) == [("org-base", 1)] and versions(after) == [("org-base", 2)]
    assert before["effective_policy_hash"] != after["effective_policy_hash"]
    assert before["policies"][0]["clauses"][3]["params"] == {"limit_minor": 5000}
    assert after["policies"][0]["clauses"][3]["params"] == {"limit_minor": 3000}
    assert "org-base v1" in before["summary"]
    assert env.tl.policy_as_of("mission", "m1", T(8, 30), "valid")["attachments"] == []


def test_attach_detach_windows(env):
    env.publish_attach(T(9), 5000, 1)
    env.at(T(9, 5))
    env.reg.publish("extra", "Extra", [Clause("X-1", "no x", "forbid_input", {"key": "x"})])
    env.at(T(10))
    att = env.reg.attach("mission", "m1", "extra", 1, 0)
    env.at(T(11))
    env.reg.detach(att.attachment_id, 1)
    ids = lambda t: sorted(a["policy_id"] for a in env.tl.policy_as_of("mission", "m1", t)["attachments"])
    assert ids(T(9, 30)) == ["org-base"]
    assert ids(T(10, 30)) == ["extra", "org-base"]
    assert ids(T(11, 30)) == ["org-base"]
    assert ids(T(10, 30)) == ["extra", "org-base"]  # replay is stable
    # other missions never saw it
    assert [a["policy_id"] for a in env.tl.policy_as_of("mission", "m2", T(10, 30))["attachments"]] == ["org-base"]


def test_recorded_basis_uses_transaction_time(env):
    env.publish_attach(T(9), 5000, 1)
    first_ts = env.audit.query(kind="policy.attach")[0]["ts"]
    # v2 is recorded now but declared valid retroactively at 09:30 (a late correction)
    env.publish_attach(T(9, 30), 3000, 2)
    # what was in force at 09:45 really: v2; what the system knew at first_ts: v1 only
    assert versions(env.tl.policy_as_of("mission", "m1", T(9, 45), "valid")) == [("org-base", 2)]
    assert versions(env.tl.policy_as_of("mission", "m1", first_ts, "recorded")) == [("org-base", 1)]


def test_decisions_reproduce_via_reevaluate(env):
    env.publish_attach(T(9), 5000, 1)
    env.decide("m1", T(10), 4000)
    env.decide("m1", T(10, 5), 9000, rev=2)  # hold: over limit
    env.decide("m1", T(10, 6), 4000, rev=3, extra={"prohibited_zone": True})  # deny
    env.publish_attach(T(12), 3000, 2)
    env.decide("m2", T(13), 4000)  # hold under v2
    decisions = env.audit.query(kind="decision")
    assert [d["policy"]["verdict"] for d in decisions] == ["allow", "hold", "deny", "hold"]
    for d in decisions:
        r = reevaluate_recorded(env.audit, d)
        assert r["reproduced"], r["mismatches"]
        assert r["verdict"] == d["policy"]["verdict"]
    # pure: same call, same answer; and a different snapshot gives a different answer
    snap = env.tl.snapshot(None, before_seq=decisions[0]["seq"] + 1)
    assert reevaluate(decisions[0], snap) == reevaluate(decisions[0], snap)
    later = env.tl.snapshot(T(13))
    assert not reevaluate(decisions[0], later)["reproduced"] or later.get("org-base").version == 1


def test_reevaluate_ignores_approval_annotations(env):
    env.publish_attach(T(9), 5000, 1)
    seq = env.decide("m1", T(10), 9000)
    d = env.audit.query(kind="decision")[0]
    d["policy"]["clauses"] = [
        {**r, "outcome": "allow", "evidence": {**r["evidence"], "approved_by": "bob", "overridden_outcome": "hold"}}
        if r["clause"] == "APPROVE-1" else r for r in d["policy"]["clauses"]]
    d["policy"]["verdict"] = "allow"
    assert seq and reevaluate_recorded(env.audit, d)["reproduced"]


def test_divergence_allowed_v1_hold_v3_limit_lowered(env):
    env.publish_attach(T(9), 5000, 1)
    env.decide("m1", T(10), 4000)
    env.publish_attach(T(11), 4500, 2)
    env.publish_attach(T(12), 3000, 3)
    h = payment_history(env.audit, "m1")
    div = h["divergence"]
    assert div["allowed_then"] is True and div["would_now"] == "hold" and div["diverged"]
    assert [c["clause"] for c in div["differing_clauses"]] == ["APPROVE-1"]
    assert "lowered from 5000 to 3000" in div["differing_clauses"][0]["why"]
    assert "lowered" in div["summary"]
    dec = next(i for i in h["timeline"] if i["kind"] == "decision")
    assert dec["policy_in_force"]["policies"][0]["version"] == 1 and dec["reproduced"]
    # a live registry as "current" gives the same answer
    live = payment_history(env.audit, "m1", current=env.reg)["divergence"]
    assert live["would_now"] == "hold"


def test_no_divergence_when_policy_unchanged(env):
    env.publish_attach(T(9), 5000, 1)
    env.decide("m1", T(10), 4000)
    assert payment_history(env.audit, "m1")["divergence"]["diverged"] is False


def test_late_event_valid_vs_transaction_order_and_chain(env):
    env.publish_attach(T(9), 5000, 1)
    a = env.audit.append("system", "input.event", "position fix", mission_id="m1", valid_at=T(10),
                         payload={"patch": {"location": "a"}})
    b = env.audit.append("system", "input.event", "trip start", mission_id="m1", valid_at=T(11),
                         payload={"patch": {}})
    c = env.audit.append("receiver", "input.event", "receipt confirmed (arrived late)", mission_id="m1",
                         valid_at=T(10, 30), payload={"patch": {"receipt_confirmed": True}})
    fix = env.audit.append("system", "input.event", "correction: fix was at 10:20", mission_id="m1",
                           valid_at=T(10, 20), supersedes=a, payload={"patch": {"location": "a2"}})
    h = payment_history(env.audit, "m1")
    o = h["orderings"]
    assert o["by_transaction_time"] == [a, b, c, fix]
    assert o["by_valid_time"] == [a, fix, c, b]
    assert [x["seq"] for x in h["late_arrivals"]] == [c, fix]
    by_seq = {i["seq"]: i for i in h["timeline"]}
    assert by_seq[c]["late_arrival"] and not by_seq[b]["late_arrival"]
    assert by_seq[fix]["supersedes"] == a and by_seq[c]["valid_at"] == T(10, 30)
    assert by_seq[c]["recorded_at"] > by_seq[c]["valid_at"] or by_seq[c]["recorded_at"]  # both times present
    assert env.audit.query(mission_id="m1")[0]["valid_at"] == T(10)  # original never edited
    assert env.audit.verify()["ok"]
    env.led.db.execute("UPDATE audit_log SET valid_at=? WHERE seq=?", (T(1), a))
    assert env.audit.verify() == {"ok": False, "broken_at_seq": a, "entries": a - 1}


def test_old_chain_verifies_and_db_migrates(tmp_path):
    db = sqlite3.connect(tmp_path / "old.sqlite")
    db.row_factory = sqlite3.Row
    db.executescript("""CREATE TABLE audit_log(
      seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, party TEXT, mission_id TEXT,
      kind TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL DEFAULT '{}', policy TEXT,
      stripe TEXT, mode TEXT, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);""")
    import hashlib
    import json
    row = {"ts": "t", "actor": "system", "party": None, "mission_id": "m", "kind": "x", "summary": "s",
           "payload": {}, "policy": None, "stripe": None, "mode": None}
    h = hashlib.sha256(("0" * 64 + json.dumps(row, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()
    db.execute("INSERT INTO audit_log(ts,actor,mission_id,kind,summary,payload,prev_hash,hash) VALUES('t','system','m','x','s','{}',?,?)",
               ("0" * 64, h))
    log = AuditLog(db)
    assert log.verify()["ok"]
    log.append("system", "y", valid_at=T(1), supersedes=1)
    assert log.verify() == {"ok": True, "entries": 2, "head": log.verify()["head"]}
    assert log.query(kind="y")[0]["supersedes"] == 1


def test_secrets_redacted_in_policy_events(env):
    env.at(T(9))
    env.reg.publish("p", "title sk_test_ABCDEFGH1234", [Clause("A-1", "uses whsec_ABCDEFGHIJ", "forbid_input", {"key": "k"})])
    blob = str(env.audit.query(kind="policy.publish"))
    assert "sk_test_ABCDEFGH1234" not in blob and "whsec_ABCDEFGHIJ" not in blob and "[REDACTED]" in blob


def test_instrument_preserves_behaviour_and_backfills(tmp_path):
    led = Ledger(tmp_path / "t.sqlite")
    audit = AuditLog(led.db)
    from treasury.policies import default_registry
    reg = default_registry()
    assert instrument_registry(reg, audit) is reg and instrument_registry(reg, audit) is reg
    assert [e["kind"] for e in audit.query()] == ["policy.publish", "policy.attach"]
    b = reg.publish("z", "Z", [Clause("Z-1", "z", "forbid_input", {"key": "z"})])
    assert b.version == 1 and audit.query(kind="policy.publish")[-1]["payload"]["bundle"]["policy_id"] == "z"
    # restart: a fresh registry is backfilled without duplicating the identical seed
    reg2 = default_registry()
    instrument_registry(reg2, audit)
    assert len(audit.query(kind="policy.publish")) == 2
    r = PolicyTimeline(audit).policy_as_of("mission", "m", "2999-01-01T00:00:00+00:00")
    assert [a["policy_id"] for a in r["attachments"]] == ["org-base"]


def test_routes(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from treasury.routes_history import router
    env.publish_attach(T(9), 5000, 1)
    env.decide("m1", T(10), 4000)
    env.publish_attach(T(12), 3000, 2)
    app = FastAPI()
    app.state.audit = env.audit
    app.include_router(router)
    c = TestClient(app)
    r = c.get("/history/policy-as-of", params={"target_type": "mission", "target_id": "m1", "at": T(10), "basis": "valid"})
    assert r.status_code == 200 and r.json()["attachments"][0]["version"] == 1
    assert c.get("/history/policy-as-of", params={"target_type": "mission", "target_id": "m1", "at": "nope"}).status_code == 400
    assert c.get("/history/policy-as-of", params={"target_type": "mission", "target_id": "m1", "at": T(1), "basis": "x"}).status_code == 400
    p = c.get("/history/payment/m1").json()
    assert p["divergence"]["would_now"] == "hold" and p["timeline"]
    assert "orderings" in c.get("/history/mission/m1").json()
