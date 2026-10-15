# ATREAS-15 — Frontend: draw chained policies and attach them to payment cards

**Stream:** frontend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Build the policy composer and attachment UI: select a payment/job/card, add or chain rules, preview which clauses pass, and activate a validated new version. Policy paths and unmet obligations are visible graphics rather than hidden JSON.

**Blocked by:** ATREAS-14.

## Acceptance criteria

- [ ] Amount/invoice, insurance, travel, fuel, receipt and approval nodes can be added and connected; render all-of joins, versions, inherited mandatory rules and payment attachments.
- [ ] Show server validation, cycles/type errors, before/after impact and stale-edit conflict. Preview never sends payment/activation commands.
- [ ] Editing a pending payment displays invalidated unused authority; completed payments retain the original rule/evidence inspector. Component fixtures conform to the shared contract.

## Planned validation

These targets are not implemented or run yet.

- `frontend/src/__tests__/policy-graph.test.tsx::chainAndAttach`
- `frontend/e2e/policies.spec.ts::previewAndActivate`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
