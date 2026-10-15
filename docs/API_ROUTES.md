# API routes for Agent Treasury

Researched 15 October 2026 using official documentation. This is a capability map, not evidence that a particular account has access. No authenticated Stripe requests have been made.

## Money routes

| API | Actual source → destination | Planning consequence |
|---|---|---|
| PaymentIntent / Checkout | Customer payment method → merchant Stripe balance | Merchant-side collection; not a general supplier bank-transfer API. [PaymentIntents](https://docs.stripe.com/api/payment_intents/create) |
| Connect Transfer | Platform Stripe balance → connected Stripe account | Route among onboarded recipients, including several recipients for one business operation. [Create transfer](https://docs.stripe.com/api/transfers/create) |
| Payout | Stripe account balance → its eligible external bank account/card | Use connected-account context for a recipient's payout; do not substitute arbitrary supplier bank details. [Create payout](https://docs.stripe.com/api/payouts/create) |
| Refund | Original card charge → original charged card | May be partial, bounded by unrefunded amount; cannot redirect to a different supplier. [Create refund](https://docs.stripe.com/api/refunds/create) |
| Transfer reversal | Original connected-account transfer → platform balance | Correct/refund eligible transfers, subject to recovery conditions. [Reversal API](https://docs.stripe.com/api/transfer_reversals/create) |
| Issuing | Issuing balance → card purchase | UK sandbox supports simulated merchant authorization and capture; test cards cannot buy externally. [Testing](https://docs.stripe.com/issuing/testing?testing-method=with-code) |
| Shared Payment Token | Customer payment method → scoped seller via seller PaymentIntent | Credential can constrain seller/currency/amount/expiry. Token creation is not payment. [SPT](https://docs.stripe.com/agentic-commerce/concepts/shared-payment-tokens?agent-seller=agent) |
| MPP | Agent credential → paid service and Stripe receipt | HTTP 402 challenge, pay/retry, receive resource. Sandbox roundtrips documented. [MPP](https://docs.stripe.com/payments/machine/mpp) |
| Treasury v2 OutboundPayment | Financial account → third-party bank account or another owner's financial account | UK routes exist, but contact-sales access is required. [Treasury](https://docs.stripe.com/treasury/connect) |

## Connect details that affect a replan

Separate charges and transfers decouples collection from the chosen recipients. A transfer group associates objects; it does not enforce a budget or reserve funds. A source transaction ties transfer availability to an existing charge. Refunding a separate charge does not automatically reverse its transfers. Recovery has conditions, including recipient balance/reserves, and is not a general undo button. [Separate charges and transfers](https://docs.stripe.com/connect/separate-charges-and-transfers?locale=es-ES)

Test funding should be established before recording. Stripe documents special test cards that put charge funds directly into the available test balance; ordinary successful charges can enter pending balance. [Available test balance](https://docs.stripe.com/testing#available-balance)

An authorization and capture split can reserve an eligible customer payment method before collecting it, but that is merchant-side payment processing. It is distinct from an Issuing authorization on a buyer's issued card. [Manual capture](https://docs.stripe.com/payments/place-a-hold-on-a-payment-method)

## Memory, bookkeeping and orchestration

Cognee REST exposes add, cognify and search endpoints under /api/v1. Cloud uses an X-Api-Key header. Store policy versions, source clauses and decision evidence; retrieve relevant history for explanations. Keep authoritative financial state and deterministic policy checks in our own ledger. Processing latency and fresh-policy retrieval need an integration check; do not assume add makes a new policy immediately queryable. [Cognee REST](https://docs.cognee.ai/api-reference/introduction)

Briefcase MCP exposes bookkeeping reads/writes, documents, expense claims and Ledger reports/journals. Its public documentation explicitly excludes money movement. It can supply evidence or record outcomes if an account is available; it cannot replace Stripe execution. [Briefcase MCP](https://briefcase.so/mcp)

Stripe Financial Connections supplies permissioned account data and verification used by other payment products, not a standalone general payment rail. [Financial Connections](https://docs.stripe.com/financial-connections)

Stripe MCP is a tool interface to underlying Stripe APIs. Its user-authenticated write actions can require human confirmation. Prefer explicit SDK operations inside policy-controlled DBOS execution steps for the proposed application, preserving required human actions for any selected product. This is an architecture recommendation. [Stripe MCP](https://docs.stripe.com/mcp)

The selected architecture uses a small application-owned GOAP planner and DBOS workflows/steps. Checkpoint nondeterministic inputs, preserve confirmed effects across replans and use explicit compensation branches. [DBOS workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial), [DBOS compensation example](https://docs.dbos.dev/python/examples/widget-store)

## Explain every pound

Record semantic operation ID, policy version/clause, counterparty, invoice/quote evidence, approvals, amount/currency, Stripe account context, Stripe object IDs, status, idempotency key and plan lineage. Reconcile fees and net balance impact using Stripe balance transactions. [Balance transaction](https://docs.stripe.com/api/balance_transactions/object)

Webhook delivery can be duplicated and out of order. Verify signatures, deduplicate event IDs and retrieve current objects when required. A timeout is an unknown outcome, not a failed payment. Use persistent operation IDs plus idempotency keys and reconciliation, rather than assuming retries are permanently deduplicated. [Webhooks](https://docs.stripe.com/webhooks), [Idempotency](https://docs.stripe.com/api/idempotent_requests)

## First account checks

- Connect: create eligible test recipients, establish test balance, prove one GBP transfer, inspect destination and balance transaction.
- Issuing: enable test access, fund test balance, prove permitted and declined authorizations plus a captured transaction.
- Refund: create an original test payment, prove a partial refund and inspect the refund object.
- Contractor payout: prove a transfer to a distinct contractor account; add a bank payout only when its setup and status can be demonstrated reliably.
- Treasury/SPT/MPP: check access before expanding the core project onto those products.
