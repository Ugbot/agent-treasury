# ATREAS-10 — Refund a policy-approved canceled delivery without duplicate money movement

**Stream:** backend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Cancel a separate funded order before pickup when no eligible driver or route exists. The refund agent evaluates its attached policy, refunds the original payment and explains the decision in the map/ledger.

**Blocked by:** ATREAS-3, ATREAS-5, ATREAS-7.

## Acceptance criteria

- [ ] A pre-pickup canceled GBP100 order with no merchant/driver transfers receives one full refund to its original payment method, capped by remaining refundable value.
- [ ] Already delivered, custody-ambiguous, already refunded or uncertain-outcome orders do not receive a blind automatic refund; they follow hold/escalation.
- [ ] Refund state and provider references appear with exact policy clause, original collection and cancel evidence; duplicated cancel inputs create one semantic refund.
- [ ] Refunds do not automatically restore spending allowance or reverse separate Connect transfers; any captured fuel cost and processing fees remain explicit.
- [ ] Browser shows refund submitted/pending/confirmed/failed accurately and does not declare success at request dispatch.

## Planned validation

These targets are not implemented or run yet.

- `tests/test_refunds.py::test_canceled_before_pickup_refunds_once`
- `tests/test_refunds.py::test_unknown_or_settled_order_holds_refund`
- `tests/live/test_refunds.py::test_original_test_payment_refunded`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
