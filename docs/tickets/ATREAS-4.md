# ATREAS-4 — Attach versioned Cognee policies to payments and entitlements

**Stream:** backend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Create written policy bundles in Cognee and attach exact versions to payments, jobs, cards or counterparties. Inspect the effective rules and evidence on any operation; deterministic evaluation uses a validated immutable snapshot rather than treating semantic search text as executable authority.

**Blocked by:** ATREAS-2.

## Acceptance criteria

- [ ] Create a policy with stable policy/clause IDs, prose, typed rule data, content hash and version; ingest/process it in Cognee and expose memory readiness/source references.
- [ ] Attach or detach optional policies on a pending payment/job/card/counterparty using expected-version concurrency control; mandatory organisation rules cannot be detached or weakened.
- [ ] Effective rules combine mandatory/inherited and explicit attachments restrictively. A permissive attached policy cannot override a denial or bypass an approval/evidence obligation.
- [ ] Every decision/operation records attachment versions, effective policy hash, input revision and clause/evidence results; unknown/stale required policy pauses payment and completed decisions remain immutable.
- [ ] Browser displays attached/effective policies and a why-held/why-paid explanation. Missing Cognee configuration is visible and never represented as a successful memory write.

## Planned validation

These targets are not implemented or run yet.

- `tests/test_policies.py::test_effective_attachments_are_restrictive`
- `tests/test_policies.py::test_stale_policy_holds_payment`
- `tests/test_memory.py::test_versioned_policy_roundtrip`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
