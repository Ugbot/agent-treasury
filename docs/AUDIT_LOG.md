# Audit log

Append-only, hash-chained record of every input event, decision, agent proposal, Stripe call,
approval, replan and memory read/write. Canonical source for "why was this paid?". Code: `src/treasury/audit.py`
(writer) and `src/treasury/routes_audit.py` (readers).

## Fields (table `audit_log`)
`seq` (monotonic id), `ts` (UTC ISO), `actor`, `party`, `mission_id`, `kind`, `summary`, `payload` (JSON),
`policy` (JSON: id, version, hash, verdict, clauses), `stripe` (JSON: ids, livemode, op), `mode`, `prev_hash`, `hash`.

## Actors
system, agent, planner, sender, receiver, driver, approver, stripe, cognee.

## Kinds (free-form, keep stable)
input.event, policy.publish, policy.attach, decision, plan, replan, agent.proposal, agent.verdict, approval,
stripe.call, saga.step, saga.compensation, memory.write, memory.read, chat.explain, delivery.proof, fuel.auth, refund.

## Hash chain
`hash = sha256(prev_hash + canonical_json(row))`, where the row is ts, actor, party, mission_id, kind, summary,
payload, policy, stripe, mode (sorted keys, compact separators). The first `prev_hash` is 64 zeros.
`verify()` recomputes the whole chain and reports `{ok:false, broken_at_seq}` on the first edited, reordered or
removed entry (deleting the newest entries is not detectable without an external anchor: publish the head hash).
Secrets (`sk_`, `rk_`, `pk_`, `whsec_` tokens) are redacted at write time.

## Routes (mount `router` from `routes_audit.py`, optionally under `/api`)
Responses use the `{data}` / `{error:{code,message}}` envelope except export and stream.

| Route | Purpose |
| --- | --- |
| `GET /audit` | filters `mission`, `party`, `kind`, `actor`, `after` (seq), `limit` (max 1000); returns `entries`, `next_after` |
| `GET /audit/export` | same filters, NDJSON, one entry per line |
| `GET /audit/verify` | chain check; `head` only for admin |
| `GET /audit/party/{party}` | one party's entries, role scoped |
| `GET /audit/stream` | SSE, `event: audit`, `id: <seq>`; resume with `?after=` or `Last-Event-ID` (larger wins); ordered, each seq once; `follow=false` drains and closes |

Wiring: set `app.state.ledger = ledger` (or `app.state.audit = AuditLog(ledger.db)`); `get_audit(app.state)` creates
the single `AuditLog` on the ledger connection.

## Demo auth and scoping
**Demo auth only, not production auth.** Identity is read from headers anyone can set:
`X-Party-Role` (sender, receiver, driver, approver, admin) and `X-Party-Id` (required unless admin).
Missing headers give 401, an unknown role 403. Non-admin callers only receive entries whose `party` equals their
`X-Party-Id`; asking for another party gives 403. For non-admins, `policy` is reduced to id/version/verdict,
`stripe` to ids/livemode/op, payload keys starting with `_` are dropped, and `prev_hash` is omitted.
Admin sees everything unmodified. Entries with no party are admin-only.
