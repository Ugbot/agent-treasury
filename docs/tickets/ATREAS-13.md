# ATREAS-13 — Rehearse the integrated demo and publish the OSS submission

**Stream:** release

**Status:** open; ready-for-agent label. Readiness depends on the blockers below.

**What to build:** Polish the policy-first web console, rehearse the four scenarios plus live policy attachment/chaining, and package a reproducible public MIT-licensed repo with a <=2-minute Stripe test-mode demo video.

**Blocked by:** ATREAS-6, ATREAS-8, ATREAS-9, ATREAS-10, ATREAS-11, ATREAS-12, ATREAS-19.

## Acceptance criteria

- [ ] Browser visibly connects input changes, attached/chained rules, deterministic decisions, GOAP plan changes, DBOS execution status and real Stripe test ledger objects.
- [ ] Exercise delivery release, fuel authorization/capture, coverage/route replan, cancellation refund, invoice threshold and a grounded AI draft; select a legible recording sequence <=120 seconds.
- [ ] Fresh-clone instructions, pinned dependency locks, example configuration without credentials, written policies and automated checks reproduce the app; real integration claims include evidence.
- [ ] Verify no live keys/PII/private receipts in staged/public artifacts, run appropriate dependency audits, and keep map/coverage/receipt simulation labels visible.
- [ ] Create/push the public GitHub repo, record an accessible video and prepare required submission fields. Publishing/submitting uses the user-authorised destination and confirms resulting public URLs; do not mark done before external artifacts exist.

## Planned validation

These targets are not implemented or run yet.

- `tests/e2e/demo.spec.ts::policyDrivenPaymentsJourney`
- `tests/integration/test_full_demo.py::test_all_financial_routes`
- `manual::fresh_clone_and_public_submission`

## Context

See [plan](../PLAN.md), [policy model](../POLICY_MODEL.md), [contract](../API_CONTRACT.md) and [implementation steps](../superpowers/plans/2026-10-15-agent-treasury.md). Backend owns authority and provider state; frontend owns triggers and graphics.
