# ATREAS-11 — Recover and reconcile payment sagas across crashes and partial failures

**Stream:** backend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Interrupt payment workflows at meaningful fault points and recover them with DBOS. Explicit compensating steps cancel unused authority or request permitted reversals/refunds; the ledger exposes unresolved compensation instead of declaring success.

**Blocked by:** ATREAS-6, ATREAS-8, ATREAS-9, ATREAS-10.

## Acceptance criteria

- [ ] Crash after provider success but before DBOS checkpoint resumes using the same semantic operation/idempotency key and reconciliation; no duplicate invoice, transfer, capture or refund.
- [ ] Record intended operation and reserve authority atomically before external I/O; each operation and compensation has a distinct stable identity and validated unchanged parameters.
- [ ] Preserve confirmed split legs across restart/replan; invoke cancellation/refund/reversal only when the provider state and attached compensation policy permit it.
- [ ] An unrecoverable or exhausted compensation stays compensation-required with retry/escalation evidence; financial success is never inferred from an uncaught workflow error.
- [ ] Durable policy/input waits and queued events survive restart; DBOS replay does not re-read non-checkpointed nondeterministic planner inputs.

## Planned validation

These targets are not implemented or run yet.

- `tests/test_recovery.py::test_provider_success_before_checkpoint`
- `tests/test_recovery.py::test_partial_settlement_survives_replan`
- `tests/test_sagas.py::test_failed_compensation_is_visible`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
