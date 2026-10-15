# Agent Treasury: combined working idea

15 October 2026. Captures the user's selected scope and a proposed direction; this is not an approved implementation specification.

## Agreed scope

Use TaskEngine to coordinate a series of payment agents, a policy worker and live replanning. Combine all four experiences: bill paying within approval limits, capped virtual-card buying, refunds or disputes, and contractor payouts. Use a new standalone open-source repository. All financial operations in the hackathon are Stripe test-mode operations.

## Product proposal

One finance operations console and shared budget, policy and evidence ledger. Four specialist agents propose actions; one deterministic policy worker authorises them; narrowly scoped execution workers perform the corresponding Stripe operation. TaskEngine coordinates dependencies and replans the remaining work. Cognee remembers versioned policies, vendor/customer history, approval decisions and execution evidence.

A shared dashboard presents the goal, plan, spend/reservations, approval queue, Stripe references and an explanation for each financial action. Quotes, invoices and merchant events supplied by the demonstrator are labelled as such.

## Four agents

| Agent | Input and decision | Candidate Stripe path | Useful policy rules |
|---|---|---|---|
| Bills | Invoice, supplier identity, purchase evidence; pay, hold or request approval | Connect Transfer for an onboarded test supplier; Treasury OutboundPayment if access exists | Supplier allowlist, invoice matching, duplicate detection, approval threshold |
| Buyer | Purchase request and merchant quote; issue/use a capped virtual card | Issuing card, authorization and capture through test helpers | Card amount/category/country limits, internal merchant allowlist, aggregate mission budget |
| Refunds | Customer request plus original payment; refund, escalate or decline | Refund against original Charge/PaymentIntent | Maximum refundable amount, order evidence, amount-based approval |
| Contractors | Approved milestone/invoice and verified recipient; release or hold payment | Connect Transfer, optionally connected-account Payout; Treasury/Global Payouts if enabled | Approved counterparty, completed milestone, payment threshold, no duplicates |

Use refunds as the first implementation of the refunds/disputes agent. A dispute response is a different workflow from issuing a refund and is not automatically a new money movement.

## One proposed demo narrative

The agent receives a small launch-day finance packet: supplier bill, equipment purchase, customer refund and contractor milestone. It plans the four operations under one policy. After one operation completes, a finance policy update lowers the remaining authority or changes an allowed supplier. TaskEngine preserves confirmed effects, invalidates stale authorisations, and replans pending work. A permitted alternative executes; an operation with no compliant alternative waits for approval. The operator can ask why any amount moved and inspect the exact clause, evidence, policy version and Stripe object.

Keep the policy independently editable. Demonstrate autonomy by finding permitted alternatives; do not split invoices or change rails to evade an approval requirement. A customer refund does not automatically replenish the spending budget: gross/net exposure and when released funds can be reused need an explicit rule.

## Financial state and controls

- Distinguish planned, reserved, awaiting approval, submitted, uncertain, succeeded, failed, refunded and reversed operations.
- A virtual-card authorization reserves funds; capture creates a transaction. Creating a card or payment token alone does not pay anyone.
- Validate current policy and reserve budget atomically before execution; prevent parallel workers spending the same budget.
- Tie authority to operation, amount, recipient, currency and policy version. Changes invalidate unused authority.
- Keep LLM/Cognee calls outside Issuing's two-second authorization response path.
- Reconcile uncertain network outcomes before retrying or paying another recipient. Preserve confirmed operations across replans, using stable operation identifiers and Stripe idempotency keys.
- Policies, merchant invoices and model suggestions have different trust levels; invoice text cannot alter policy.
- Keep secrets in execution workers, never in Cognee memory, public logs or the Git repository.

## API alternatives worth preserving

SPT/MPP offers a separate genuine agent-to-service buying flow with seller-scoped credentials. It can be a later addition; it does not replace the user's requested capped virtual-card demonstration.

## Decisions still open

1. Confirm which Stripe sandbox products are enabled: Connect, Issuing, and optionally Treasury/Global Payouts.
2. Choose the concrete launch-day story and exact numbers, counterparty identities and approval limits.
3. Decide which existing TaskEngine runtime to depend on and pin its version/revision.
4. Define how refund/returned funds affect the shared budget.
5. Select the smallest vertical slice, then an implementation order that can demonstrate all four agents in two minutes.
6. Publish the repository publicly and record/submit the demo during the required window.
