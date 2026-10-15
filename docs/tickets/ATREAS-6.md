# ATREAS-6 — Invoice above a threshold and wait for actual payment

**Stream:** backend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Attach a collection policy that automatically creates a Stripe test invoice above a configured threshold, displays it in the mission and waits for confirmed payment before allowing downstream disbursement. Keep require-invoice-evidence for a bill distinct from issue-invoice-to-collect.

**Blocked by:** ATREAS-3, ATREAS-5.

## Acceptance criteria

- [ ] GBP150 under an invoice-above-GBP100 policy creates one finalized Stripe test invoice automatically; GBP100 follows the ordinary collection path because the condition is strictly greater-than.
- [ ] An issued invoice or hosted payment URL alone never satisfies funded; confirmed paid state and provider payment evidence unlock the next policy obligation.
- [ ] Invoice collection and separate PaymentIntent collection are mutually exclusive for the same obligation; repeated events and workflow retries cannot double bill.
- [ ] Server verifies/deduplicates Stripe events and reconciles current invoice/payment state on uncertain outcomes; browser shows issued, awaiting payment and confirmed states.
- [ ] An existing funded obligation is not billed again when a chain is edited; partial or failed collection cannot be represented as fully funded.

## Planned validation

These targets are not implemented or run yet.

- `tests/test_invoice_policy.py::test_threshold_boundary_and_no_double_collection`
- `tests/test_invoice_policy.py::test_unpaid_invoice_blocks_release`
- `tests/live/test_invoices.py::test_test_invoice_paid_unlocks_obligation`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
