# ATREAS-2 — Backend: Drive a durable payment decision from changing inputs

**Stream:** backend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Run a local mission in a browser: submit changing inputs, inspect allow/hold/deny, and execute an authorised mock payment through a DBOS workflow and persistent ledger. This is the minimal inputs -> policy -> payment vertical slice; mock operation IDs and mode must be explicit.

**Blocked by:** None — can start immediately.

## Acceptance criteria

- [ ] Browser submits an event with a unique ID and expected input revision; API validates it, persists it and delivers it to a stable DBOS workflow ID.
- [ ] An unmet rule holds the payment; a satisfying input permits it; a forbidden input denies it, each with clause ID, policy version and evidence.
- [ ] Amounts are integer minor units, positive and currency-bound. Duplicate event/start requests create one logical operation and one ledger effect.
- [ ] Restart while awaiting input resumes the existing mission using persistent SQLite. Mock receipts are visibly distinguished from Stripe test receipts.

## Planned validation

These targets are not implemented or run yet.

- `tests/test_core.py::test_inputs_control_payment`
- `tests/test_core.py::test_event_and_operation_idempotency`
- `tests/test_recovery.py::test_waiting_mission_resumes`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
