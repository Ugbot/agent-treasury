# ATREAS-12 — Backend: Use real AI chat to explain policy decisions and draft job messages

**Stream:** backend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Add a sidebar backed by a configured real model API. Operators can ask why a payment was held/paid, inspect cited policy evidence and generate editable messages for the driver, merchant or customer; messages stay as local drafts.

**Blocked by:** ATREAS-4.

## Acceptance criteria

- [ ] Actual provider call uses selected job facts, decision records and retrieved Cognee policy evidence with bounded history/tokens/timeout; provider and model are visible.
- [ ] Why-paid/held responses cite only known policy version/clause and evidence IDs, link them to the ledger and state uncertainty when supporting evidence is absent.
- [ ] Generate recipient-specific editable drafts without sending externally; generation failure and unavailable keys are shown rather than replaced with canned text labelled AI.
- [ ] Chat has no payment or policy-activation authority; instruction-like text from invoices, messages or memory cannot override executable policy.
- [ ] Use text-safe rendering, redact secrets/card data and avoid exposing precise real user location; persist draft/message IDs and generation provenance.

## Planned validation

These targets are not implemented or run yet.

- `tests/test_chat.py::test_grounded_context_and_no_write_tools`
- `tests/test_chat.py::test_missing_provider_is_explicit`
- `tests/e2e/chat.spec.ts::draftsRemainLocal`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
