# ATREAS-17 — Frontend: visualise money flows, allowances and saga evidence

**Stream:** frontend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Create payment/entitlement graphics showing customer collection or invoice, merchant/driver split, fuel-card allowance/capture, refund path and DBOS saga progress. Selecting an object opens why-paid/held evidence.

**Blocked by:** ATREAS-14.

## Acceptance criteria

- [ ] Show minor-unit amounts as GBP, gross retention/fees/net separately, real provider IDs, payment policy attachments and planned/reserved/pending/confirmed/unknown states.
- [ ] Graphically distinguish allowed, held, denied, partially settled and compensation-required flows; card creation, authorization, capture, transfer and bank payout are different states.
- [ ] Inspector links policy/clause version, input snapshot, approvals, provider receipt and plan/saga lineage without exposing secrets; invoice-created never looks like paid.

## Planned validation

These targets are not implemented or run yet.

- `frontend/src/__tests__/money-flow.test.tsx::partialAndUnknownStates`
- `frontend/e2e/evidence.spec.ts::inspectEveryPound`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
