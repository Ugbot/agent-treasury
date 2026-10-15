# Stripe specialist payment routes for the hackathon

Researched 15 October 2026 against current first-party Stripe documentation. Read-only research; no credentials, account setup, or API mutations. Account-specific enablement remains unverified.

## Decision

The most interesting additional route is **an agent purchasing paid API resources through Shared Payment Tokens (SPTs) and Machine Payments Protocol (MPP)**. This has a real buyer-to-seller payment flow, Stripe test-mode support, seller-scoped limits and receipts, and a natural replanning story: find a policy-compliant alternative service. It avoids implying that issuing a virtual card automatically lets a test agent shop at arbitrary merchants. This is a product recommendation inferred from the capabilities below; a sandbox spike must establish actual account access.

For direct bill payments, current **Treasury for platforms v2 supports GBP and UK recipients**, unlike legacy v1. It is promising but sales-gated, so avoid making it a hackathon dependency before access is proven. Ordinary Connect transfers are being researched separately.

## Route map

| Route | Source → destination | What the agent can choose | Access / UK position |
|---|---|---|---|
| SPT + seller PaymentIntent | Customer payment method → chosen seller's Stripe payment balance | Seller profile, currency, cap, expiry; issue or revoke scoped credential | GB supported; Stripe account and profile needed; sandbox examples documented |
| MPP | Agent payment credential → paid API/service; Stripe settles to service's balance | Which service to buy; whether policy permits its 402 payment challenge | Card/SPT route supports GB through SPT availability; stablecoins have separate access gates |
| Issuing | Issuing balance → merchant card purchase | Virtual card, controls, synchronous approve/decline | UK self-serve test access; production separately reviewed |
| Treasury v2 OutboundPayment | Financial account → different owner's financial account or external recipient bank account | Recipient and eligible payout method; cross-border quote where available | GB public preview; contact sales for access |
| Treasury v2 OutboundTransfer | Financial account → same owner's financial account or bank account | Internal allocation / withdrawal | Same-owner only; this is not a supplier-payment API |
| Global Payouts | Treasury financial account → third parties' bank accounts / supported payout methods | Recipient, supported destination/currency and delivery options | GB and US; Treasury activation required; PayoutIntents private preview |

Sources and limitations for each row follow.

## SPTs: scoped purchasing credentials, including UK

An agent collects a customer payment method, issues an SPT to a particular seller's `network_business_profile`, and sends the token to that seller. The seller creates its PaymentIntent. Limits include currency, maximum amount and expiration. GB is explicitly supported. Stripe documents `/v1/shared_payment/issued_tokens` with the `2026-09-30.preview` version and a test seller profile. Prerequisites include a Stripe account and Stripe profile. An unused SPT can be revoked to stop the seller creating a payment. A token can require human authentication, including 3DS; plan for a `requires_action` state.

Replanning implication: revoke an unused old seller credential, then issue a new credential for the selected alternative. Do not assume a seller-scoped token can be passed to another seller, or that revocation refunds an already created payment. These are architectural inferences from the scope and revocation semantics. [SPT agent guide](https://docs.stripe.com/agentic-commerce/concepts/shared-payment-tokens?agent-seller=agent)

## MPP: a real agent-to-service loop

A paid endpoint responds with HTTP 402 and payment details. The agent retries with a payment credential; the server creates the Stripe PaymentIntent and returns the resource with a receipt. The integration uses `mppx` and a Stripe profile ID. Stripe documents SPT card payments and stablecoins, plus `mppx validate` completing full sandbox roundtrips. The sample config detects a sandbox key; test mode uses a `profile_test_` profile. Manual SPT testing can use Link CLI after login.

This supports a concrete demo: three specialist data/API suppliers, a budget and provider allowlist, then a revoked provider or changed price causing the GOAP planner to select from another supplier. The merchant endpoints can be our own demonstrator services, labelled as such. [MPP integration and sandbox instructions](https://docs.stripe.com/payments/machine/mpp)

MPP supports cards via SPTs and stablecoins; x402 uses stablecoins. Stripe lists MPP Tempo USDC.e, MPP Solana USDC, and x402 Base USDC. Stablecoin acceptance outside the US requires an access request; it is not the low-friction UK default. [Machine payments availability](https://docs.stripe.com/payments/machine)

## Issuing: strong policy theatre, isolated test purchases

UK businesses can enable basic Issuing test access through the Dashboard; live access is separately reviewed. [Stripe support: applying for Issuing](https://support.stripe.com/questions/how-to-apply-for-issuing?locale=en-GB)

Create a cardholder and active virtual card, fund the test Issuing balance, create an authorization through `/v1/test_helpers/issuing/authorizations`, and simulate capture through its `/capture` endpoint. Capture creates an Issuing Transaction and closes the authorization. UK/EU test funding uses push funding instructions. **Test issued cards work only within your own Stripe account; they cannot make external purchases.** A test card therefore is not a universal bridge into someone else's test Checkout. [Issuing testing](https://docs.stripe.com/issuing/testing?testing-method=with-code)

Native controls cover categories, countries, card presence and spending limits. Merchant-ID controls are private preview. Controls run before the real-time webhook and can decline without sending an authorization request. Aggregated spending is best effort, with delays up to 30 seconds, and later tips/fees can exceed limits. Therefore maintain our own mission reservations rather than equating native card caps with an exact global budget invariant. [Spending controls](https://docs.stripe.com/issuing/controls/spending-controls)

The authorization webhook must return within **two seconds**, otherwise configured fallback applies. Insufficient Issuing balance declines before the request webhook. Run a deterministic policy check in the synchronous path; do LLM reasoning, Cognee retrieval and replanning outside it. UK CLI authorization fixtures have a `.gb` suffix. Declining a card purchase does not reroute the merchant's transaction: the planner must arrange another purchase attempt. [Real-time authorizations](https://docs.stripe.com/issuing/controls/real-time-authorizations)

## Treasury v2: materially broader UK routing

Current Treasury for platforms is **GB public preview**; other listed regions, including US/EU, are private preview. All supported regions require contacting sales. [Current v2 overview](https://docs.stripe.com/treasury/connect)

Legacy `/treasury/access` redirects to v1, describes US-only access, and explicitly advises against new v1 integrations. Do not carry the legacy US-only restriction into current v2 conclusions. [Legacy access warning](https://docs.stripe.com/treasury/connect/legacy/v1/access)

`POST /v2/money_management/outbound_payments` can move funds from platform to connected-account financial accounts, between different connected accounts, back to platform financial accounts, or to third-party recipients. For GBP, the docs list immediate cross-owner financial-account transfers and immediate UK Faster Payments to external recipients. Third-party recipients need a recipient-configured account and payout method. External cross-border payments require additional support access; quotes expose fees and FX and expire after five minutes. These are stronger bill-payment semantics than card authorization simulation, but access is the gating unknown. [OutboundPayments](https://docs.stripe.com/treasury/connect/moving-money/out-of/outbound-payments)

`OutboundTransfers` are exclusively same-owner transfers: another owned financial account or owned external bank account. GBP uses Faster Payments. A supplier belongs on OutboundPayments, not this endpoint. [OutboundTransfers](https://docs.stripe.com/treasury/connect/moving-money/out-of/outbound-transfers)

A sandbox with Treasury access can create v2 money-manager connected accounts. They cannot use a Stripe-hosted dashboard; the platform collects requirements. Test crediting a financial address through `/v2/test_helpers/financial_addresses/{id}/credit` supplies virtual funds. The integration guide shows an outbound payment with a receipt URL, `livemode:false`, and `cancelable:false`. [End-to-end Treasury integration](https://docs.stripe.com/treasury/connect/build-an-integration)

## Global Payouts and settlement boundaries

Global Payouts sends to third parties that need not be Stripe account holders, across more than 160 countries. GB and US businesses are listed; Treasury activation and financial-account funding are prerequisites. Account-specific eligibility still needs verification. [Global Payouts overview](https://docs.stripe.com/global-payouts)

OutboundPayment states matter for replanning: `processing` reserves outbound-pending funds; `failed` and `canceled` void the pending transaction; `returned` credits a separate return transaction; `posted` means funds left the source but does not guarantee receipt. Cancel only if `cancelable:true`. PayoutIntents can coordinate attempts but are private preview. Never treat a timeout or a still-processing attempt as permission to pay again. [Manage payouts](https://docs.stripe.com/global-payouts/manage-payouts)

Official UK test sort code `108800` with account `00012345` succeeds, while `11111116` produces `no_account` and `11111113` produces `account_closed`. This gives deterministic bank-route failure scenarios once access exists. [Global Payouts testing](https://docs.stripe.com/global-payouts/testing)

## Recommended verification spikes

1. Prove an SPT/MPP sandbox roundtrip with our own paid endpoint and inspect its Stripe PaymentIntent and receipt. Check seller profile creation and agent token issuance access before choosing this as the demo backbone.
2. Check whether an existing account has Treasury v2/Global Payouts sandbox capabilities. Public preview documentation does not prove our account is enabled.
3. If selecting Issuing, prove a GBP authorization, deterministic policy decline, replacement purchase authorization, and captured Issuing Transaction. Present merchant data as a simulated card-network purchase.
4. Keep the planner's semantic operation separate from the Stripe primitive: purchase, pay recipient, move own funds, and refund have different destinations and recovery semantics. No general-purpose API makes them freely interchangeable.
