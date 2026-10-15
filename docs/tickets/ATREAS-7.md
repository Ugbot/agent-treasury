# ATREAS-7 — Backend: Use map and coverage inputs to replan compliant actions with GOAP

**Stream:** backend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Show a simulated delivery map where travel, coverage and assignment events change policy facts. A small deterministic GOAP planner chooses a compliant route/driver or reports no plan, exposing old/new plans and the clause that caused replacement.

**Blocked by:** ATREAS-5.

## Acceptance criteria

- [ ] Map emits labelled simulated location/route/coverage events through the same versioned input path; browser animation alone never authorises a financial action.
- [ ] Changing the proposed route before pickup invalidates Driver A coverage; planner selects eligible Driver B or an allowed alternative route and updates pending fuel/payment entitlements.
- [ ] Search uses explicit action preconditions/effects, nonnegative costs, deterministic tie-breaks and finite search bounds; no compliant plan yields hold/escalation/cancel without weakening rules.
- [ ] Organisation travel exclusions apply even if the supplied demo insurance covers the zone; coverage includes job/vehicle/value/zones/expected duration.
- [ ] Completed operations survive replanning, assignment versions change on reassignment, and map replay/scrub generates no new payment commands.

## Planned validation

These targets are not implemented or run yet.

- `tests/test_goap.py::test_coverage_change_replans_remaining_actions`
- `tests/test_goap.py::test_no_permitted_plan_does_not_relax_policy`
- `tests/e2e/map.spec.ts::travelInputsControlAuthority`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
