# ATREAS-8 — Release merchant and driver payments only on valid proof

**Stream:** backend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Collect a customer payment, attach settlement policies and use assigned-driver identity, current destination position and one-use recipient confirmation to release merchant and driver Stripe test transfers. Present the sender as merchant by working assumption.

**Blocked by:** ATREAS-3, ATREAS-5, ATREAS-7.

## Acceptance criteria

- [ ] Seed a GBP100 order split GBP70 merchant, GBP20 driver, GBP10 gross platform retention before Stripe fees; show fees/net balance effects and any fuel operating cost separately.
- [ ] Outside destination radius, stale position, wrong assignment or reused/expired receipt holds or denies release even if other evidence passes; all gates must pass.
- [ ] An amount above the configured automatic transfer limit waits for operation-bound approval; changes to amount/recipient/policy invalidate the approval.
- [ ] On valid proof create two distinct Connect transfers with stable per-operation idempotency keys and recorded policy/input/evidence references; no premature transfer.
- [ ] One successful transfer and one failure is recorded as partial settlement; never call this atomic settlement or a completed bank payout, and never repay the successful leg.

## Planned validation

These targets are not implemented or run yet.

- `tests/test_delivery_proof.py::test_all_release_gates_required`
- `tests/test_settlement.py::test_partial_split_does_not_repay_success`
- `tests/live/test_settlement.py::test_two_test_transfers_after_receipt`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
