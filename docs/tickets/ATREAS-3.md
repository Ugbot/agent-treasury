# ATREAS-3 — Backend: Prove Stripe sandbox routes and expose capability readiness

**Stream:** backend

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Run a standalone capability probe against a user-configured Stripe sandbox and record which collection, Connect, Issuing and invoice routes are actually usable. Produce inspectable test receipts and redacted readiness evidence; do not substitute simulated money for missing access.

**Blocked by:** None — can start immediately.

## Acceptance criteria

- [ ] Reject live Stripe credentials and any provider object with livemode=true; secret values never enter logs, public files or Cognee.
- [ ] With explicit sandbox configuration, prove customer payment/refund, two distinct eligible Connect recipients and GBP transfer, Issuing authorization/capture, and invoice collection; report each capability separately.
- [ ] Record account context, object IDs, state, currency and funding readiness for Payments and Issuing separately; pending funds are not called available.
- [ ] Without keys or product access, report a specific unavailable capability and setup instruction; no capability is silently marked successful.

## Planned validation

These targets are not implemented or run yet.

- `tests/test_stripe_gateway.py::test_rejects_live_mode`
- `tests/live/test_stripe_routes.py::test_sandbox_routes`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
