# ATREAS-16 — Frontend: simulate travel with map triggers and policy overlays

**Stream:** frontend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Build the simulated map and movement controls as an input instrument. Draw drivers, merchant, destination, geofences, fuel stops, insurance/allowed zones, and old/replanned routes; animate movement and let the operator trigger route/coverage changes.

**Blocked by:** ATREAS-14.

## Acceptance criteria

- [ ] Run/pause and step controls emit canonical position events; route/coverage triggers show current eligibility, clause failures and a replacement plan from backend state.
- [ ] Geofence, fuel-stop corridor, assigned driver and coverage layers have legends and accessible text equivalents; simulated coordinates/evidence are labelled.
- [ ] Scrubbing/replaying changes the historical view only, emits no new financial commands, and cannot present fixture effects as Stripe transactions.

## Planned validation

These targets are not implemented or run yet.

- `frontend/src/__tests__/map.test.tsx::movementEventsAndReplay`
- `frontend/e2e/map.spec.ts::travelInputsControlAuthority`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
