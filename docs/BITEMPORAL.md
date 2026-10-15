# Bitemporal history (ATREAS-22)

Question answered: "What was the policy **at the time** this payment was decided, and would it be allowed today?"

## Two times

```
valid time (when it happened in the world; may be a simulated clock)
   10:00      10:20      10:30      11:00
    |  fix      |  fix'    |  receipt |  trip start
    v           v          v          v
 ---+-----------+----------+----------+------------------------>
    ^           ^          ^          ^
    |           |          |          |
  seq 1       seq 4      seq 3      seq 2          <- transaction order
 (recorded)  (recorded) (recorded) (recorded)         (audit_log.seq / ts)
transaction time (when the system wrote it; never changes)
```

- Transaction time is `audit_log.ts` / `seq`. Append-only, hash-chained.
- Valid time is `audit_log.valid_at` (nullable). When a writer omits it, valid time falls back to `ts` and the API says `valid_time_source: "recorded_time"`.
- A late event (`seq 3` above) is never reordered in the log. Responses show both orderings (`orderings.by_valid_time`, `orderings.by_transaction_time`) and list `late_arrivals`.
- Corrections are new entries with `supersedes=<seq>`; nothing is edited. `valid_at` and `supersedes` enter the entry hash only when set, so older chains still verify, and older DBs migrate with `ALTER TABLE` on startup.

## Policy events

`policy.publish`, `policy.attach`, `policy.detach` carry the full bundle snapshot (policy id, version, content hash, title, clause prose and typed params) plus attachment details. History never reads the live registry.

`policy_as_of(target_type, target_id, at, basis)` rebuilds a throwaway `PolicyRegistry` from those snapshots and uses its own `effective_attachments`/`decide`, so org (mandatory) -> job -> card/counterparty -> payment resolution and mandatory rules cannot drift.

- `basis=valid`: events whose valid time <= `at`, replayed in valid-time order ("what was really in force", including retroactive corrections recorded later).
- `basis=recorded`: events recorded <= `at`, replayed in log order ("what the system knew then").

## Payment replay

`payment_history(audit, mission_or_payment_id)` merges inputs, decisions, approvals, agent proposals and Stripe calls. Each item has `recorded_at`, `valid_at`, and `policy_in_force` (versions + hashes at its valid time). Each decision carries:

- `reproduced`: `reevaluate(entry, snapshot)` is a pure function of (policy snapshot, recorded inputs, revision, amount, recorded attachment set); it must reproduce the recorded clause outcomes and evidence (approval/override/sticky annotations are stripped, they are ledger state).
- `divergence`: recorded verdict vs re-evaluation under current policy: `allowed_then`, `would_now`, `differing_clauses[{clause, then, now, why}]`, e.g. "allowed under v1, hold under v3: APPROVE-1 limit lowered from 5000 to 3000 minor units".

Decision inputs come from `payload.inputs` if present, else `payload.facts` plus `payload.currency` (what `DecisionEngine._audit_decision` writes today). Adding `inputs` to that payload makes replay exact for custom clauses.

## API

- `GET /history/policy-as-of?target_type=&target_id=&at=&basis=valid|recorded[&inherited=job:j1,card:c1]`
- `GET /history/payment/{id}` (timeline + divergence; id may be a mission id or an operation id)
- `GET /history/mission/{id}` (adds `orderings`, `late_arrivals`)

Admin/audit view; demo-auth scoping is not applied. Hook-up: `instrument_registry(registry, audit)` once at startup (also backfills the seeded org policy).

## Wiring and UI (F3)

- `api.create_app` mounts `routes_history.router` at root and `/api`, and calls `instrument_registry(registry, audit)` once after the registry exists.
- `Ledger.apply_event(..., valid_at=)` forwards simulated-clock valid time to `audit.append`. `DecisionEngine._audit_decision` now writes `payload.inputs` so replay is exact.
- Frontend: `frontend/src/history/` (`PaymentHistory`, HTTP and fixture clients). Reachable from the app bar ("History") and the "Why was this paid?" drawer ("View history"). Clients send `X-Party-Role: admin` / `X-Party-Id: console` (demo auth).
