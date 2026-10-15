# Scenarios, money and acceptance

All people, map positions, coverage records and receipt confirmations are synthetic simulation inputs. Stripe money operations are real test API calls. The core presentation is always input -> policy -> decision -> operation -> evidence.

## Seed world and money

Use a synthetic city in metre coordinates. Merchant is (100,500); destination is (900,300); destination radius is 100m. A fuel stop sits on a seeded permitted route. Driver A initially covers route zones A/B; Driver B covers A/B/C. Organisation permits A/B/C but excludes X. A proposed route through C invalidates Driver A, while a route through X is forbidden for every driver regardless of cover. Reassignment occurs before pickup.

Default goods order: customer GBP100, merchant GBP70, driver GBP20, platform gross GBP10 before Stripe fees. Fuel purchase GBP5, capped at GBP8, is an operating cost backed by a separately funded Issuing balance. Show fuel cost and fees, not fictional GBP10 net profit. Prefund the sandbox correctly; a customer charge does not automatically fund Issuing.

## Delivery release

Collect customer payment. Attach current travel/coverage/receipt/settlement policies. An outside-radius attempt with a receipt confirmation fails; use a fresh nonce when at destination. Assigned driver, fresh position and one-use confirmation all pass; backend creates GBP70 and GBP20 Connect transfers. Record provider IDs and policy/input snapshot. A second receipt event never pays again.

## Fuel entitlement

On an active eligible assignment at an allowed fuel stop, a GBP5 fuel-merchant test authorization/capture succeeds. GBP9, a non-permitted category or an out-of-route attempt fails. Show card controls and the actual captured Issuing Transaction. Labels explain the simulated merchant and test-card boundary.

## Replanning

Before pickup, trigger a route change from A/B into C. Coverage gate rejects Driver A; basic GOAP selects Driver B or a genuinely allowed alternate route. Show failed clause, invalidated unused entitlement, new assignment/plan and retained completed effects. Route X produces hold/escalation/no-plan rather than a weaker policy. Change the fuel limit from GBP8 to GBP6 as another runtime policy example; any pending GBP7 authority becomes invalid.

## Cancellation refund

Create a separate funded order before pickup. Remove every eligible driver/route. The cancel/refund policy permits a full original-payment refund only when no recipient transfers exist. Display provider pending/confirmed status accurately and preserve any operating costs. Never reset a live mission/provider ledger to simulate a fresh run.

## Policy attachment and invoice chain

On a new GBP150 collection obligation, attach the invoice-above-GBP100 policy and chain it to funded -> coverage -> receipt -> settlement. The agent creates one Stripe test invoice and waits. An invoice link is not a paid input. Complete its test payment through Stripe, verify state and continue. At GBP100 no invoice is raised under strict greater-than semantics. A policy edit on an already funded order never re-collects the same amount.

A GBP150 recipient disbursement independently exercises the over-GBP100 approval rule. Collection invoice threshold and outgoing approval threshold apply to different action scopes even if their example numbers match.

## AI and messages

Ask why a payment is held or why fuel was allowed. Actual model output cites retrieved policy and persisted evidence. Generate an editable local driver message explaining reassignment/allowance or a customer message explaining refund progress. No external messaging channel is connected or claimed.

## Recovery

Crash/restart at a durable wait, after Stripe accepted a call before checkpoint, and after one split leg succeeds. Resume without duplicate collection/payment. A failing permitted compensation remains compensation-required with evidence; never present it as fully recovered.

## Submission

The [brief](https://hackathons.briefcase.so/stripe-october-2026/) awards 35 points for end-to-end operation, 25 for controlled autonomy, 20 for explanations and 20 for product/creativity. The [submission page](https://hackathons.briefcase.so/stripe-october-2026/submit/) requires a public GitHub repository and video of at most two minutes.

Aim for 0-15s core/payment attachment, 15-35s live chain and invoice gate, 35-55s coverage input and GOAP replan, 55-75s fuel capture, 75-100s receipt-triggered split, 100-110s cancellation refund and 110-120s why-paid/AI draft. Rehearse this; shorten transitions before removing provider evidence. Keep a longer local scenario walkthrough for Q&A. Recovery tests can support the recording without consuming every second of it.

## Evidence checklist

- Actual test PaymentIntent/invoice paid evidence, two Connect Transfers, Issuing capture Transaction and Refund.
- IDs, account contexts, livemode=false, precise status, fees/balance state.
- Cognee version/source references, effective attachment hash, clause and evidence IDs.
- DBOS workflow/operation/compensation lineage and duplicate/restart tests.
- Actual model provider/model provenance and no external message sending.
- Reproducible fresh clone, MIT licence, pinned dependency locks, ignored credentials, public repo URL and accessible <=120-second video URL.

Tests and integration evidence are planned, not yet run. Missing account access is a setup gate to report, not evidence to fabricate.
