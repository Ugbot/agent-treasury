# ATREAS-5 — Add and chain policies live without bypassing payment gates

**Stream:** backend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Use a simple browser policy composer to add typed conditions and obligations, chain them, validate a version, preview its effect and activate it for pending actions. GOAP can fulfil outstanding obligations but cannot reinterpret or remove them.

**Blocked by:** ATREAS-4.

## Acceptance criteria

- [ ] Composer supports amount thresholds, insurance coverage, travel zone, fuel allowance, recipient confirmation and human approval nodes with explicit all-of joins and typed actions.
- [ ] Reject cycles, unknown operators/effects, invalid units and incompatible action scopes; bound the graph to 32 nodes and 16 depth; never execute user code.
- [ ] An example chain amount > GBP100 -> issue invoice -> payment confirmed -> coverage -> receipt -> release is inspectable, versioned and evaluated deterministically.
- [ ] Activating or attaching a new version invalidates unused authority, re-evaluates pending operations and preserves confirmed Stripe effects/history; concurrent stale edits return conflict.
- [ ] Show before/after decision diff, unmet obligations and exact clause path; replaying the editor or its preview cannot create provider payments.

## Planned validation

These targets are not implemented or run yet.

- `tests/test_policy_graph.py::test_chain_requires_all_obligations`
- `tests/test_policy_graph.py::test_rejects_cycles_and_bad_types`
- `tests/test_policy_graph.py::test_activation_invalidates_unused_authority`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
