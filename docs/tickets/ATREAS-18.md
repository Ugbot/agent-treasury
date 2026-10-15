# ATREAS-18 — Frontend: add the AI chat sidebar and editable message drafts

**Stream:** frontend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Build the operator chat sidebar with job-context questions, cited explanations and editable driver/merchant/customer message drafts. Wire the approved chat contract and make real-provider versus unavailable state explicit.

**Blocked by:** ATREAS-14.

## Acceptance criteria

- [ ] Ask why a payment is paid/held and navigate returned citations into the policy/evidence inspector; show provider/model and generation status.
- [ ] Generate, edit and copy local recipient-specific drafts; no external send action or policy/payment authority is introduced.
- [ ] Unavailable provider, timeout and missing evidence have visible states; render text safely, bound history and support keyboard/screen reader use.

## Planned validation

These targets are not implemented or run yet.

- `frontend/src/__tests__/chat.test.tsx::draftAndCitationLinks`
- `frontend/e2e/chat.spec.ts::draftsRemainLocal`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
