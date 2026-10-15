# ATREAS-9 — Backend: Issue a trip fuel allowance and enforce it through Issuing

**Stream:** backend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Attach travel/insurance/fuel policies to a trip card. An eligible active driver at a permitted fuel stop receives capped spending authority; demonstrate one captured fuel purchase and denied invalid attempts in Stripe Issuing test mode.

**Blocked by:** ATREAS-3, ATREAS-5, ATREAS-7.

## Acceptance criteria

- [ ] Eligible active assignment may spend up to GBP8 at configured fuel categories/countries and permitted simulated route stops; GBP5 authorization/capture succeeds.
- [ ] Over-limit, non-permitted merchant category, out-of-route, stale input, invalid coverage and inactive/reassigned/completed job attempts are rejected.
- [ ] Card is backed by separately funded Issuing test balance; creation alone is not called spending, and capture is linked to its test Issuing Transaction.
- [ ] Prepare deterministic authorization state outside the two-second webhook path, maintain atomic local reservations and validate provider signatures; no LLM/Cognee call blocks the authorization response.
- [ ] Deactivation/reassignment revokes unused authority while outstanding authorizations/captures remain reconciled; labels explain test merchant simulation and merchant-category versus basket contents.

## Planned validation

These targets are not implemented or run yet.

- `tests/test_fuel_policy.py::test_trip_fuel_entitlement_boundaries`
- `tests/test_fuel_policy.py::test_concurrent_attempts_share_cap`
- `tests/live/test_issuing.py::test_capture_and_policy_declines`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
