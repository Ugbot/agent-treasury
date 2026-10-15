# ATREAS-19 — Frontend: integrate live triggers and record the policy-driven scenarios

**Stream:** frontend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Connect all frontend controls and graphics to the real backend, implement the scenario director, and rehearse one legible <=120-second journey demonstrating input-controlled payments, policy attachment/chaining and the four payment examples.

**Blocked by:** ATREAS-15, ATREAS-16, ATREAS-17, ATREAS-18, ATREAS-6, ATREAS-8, ATREAS-9, ATREAS-10, ATREAS-11, ATREAS-12.

## Acceptance criteria

- [ ] Run all controls against actual API commands/state updates; stale versions, duplicate events, approvals, reconnects and provider failures render accurately without optimistic payment success.
- [ ] Scenario director covers valid delivery, early/wrong proof, travel/coverage replan, capped fuel, invoice threshold and cancellation refund; each scenario creates isolated mission IDs without deleting provider history.
- [ ] Browser end-to-end run inspects real Stripe test objects and Cognee policy references, plus a real AI draft; produce responsive polished presentation and a verified <=120-second script.

## Planned validation

These targets are not implemented or run yet.

- `frontend/e2e/demo.spec.ts::policyDrivenPaymentsJourney`
- `tests/integration/test_full_demo.py::test_all_financial_routes`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
