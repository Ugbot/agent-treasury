# ATREAS-14 — Frontend: build the trigger deck and input-to-payment timeline

**Stream:** frontend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Create the React/TypeScript simulator shell with a trigger deck and graphics showing input -> attached policy -> decision -> payment. This is the frontend foundation and owns shared presentation types/client adapters against docs/API_CONTRACT.md. Use explicit fixture mode until backend integration.

**Blocked by:** None — can start immediately.

## Acceptance criteria

- [ ] Controls emit typed, uniquely identified commands for amount, insurance, location, receipt, policy attachment and scenario selection; backend decides authority.
- [ ] Display current inputs, policy version, allow/hold/deny, pending obligations and a chronological event feed with distinct simulation/provider modes.
- [ ] Create responsive accessible shell, loading/error/disabled states, keyboard controls and a frontend test harness; no secrets or payment authority in the browser.

## Planned validation

These targets are not implemented or run yet.

- `frontend/src/__tests__/triggers.test.tsx::typedInputCommands`
- `frontend/e2e/console.spec.ts::inputDecisionTimeline`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
