# Delivery demo: payments follow travel policy

Working design, 15 October 2026. User-selected direction: a website simulates drivers moving on a map; several scenarios issue or release funds according to travel policies. This draft is for design review, not a claim of implementation.

## Product

An Uber-style delivery marketplace with TaskEngine agents coordinating collection, merchant bills, driver entitlements, fuel-card spending, proof of receipt, payouts and refunds. A shared policy worker determines whether a proposed action is permitted and returns the exact clause and evidence. Cognee remembers policy history and decisions. Replanning finds another permitted route or driver when conditions change.

Working assumption awaiting clarification: sender means the merchant selling goods; recipient/customer pays for goods plus delivery. If sender instead means the customer paying a shipping fee, change the financial split to driver payment and any permitted customer deposit refund.

## Interface

One browser console with three connected views:

1. Map: merchant, recipient, drivers, permitted zones, fuel stops and current/replacement routes. Run/pause and scenario controls generate explicit simulation events; a scrub/replay view never submits new Stripe operations.
2. Policy and plan: current clauses, driver eligibility, coverage evidence, current fuel entitlement, delivery proof checklist and TaskEngine plan changes. Failed checks name the unmet condition.
3. Money and evidence: planned/reserved/submitted/confirmed funds, approvals, card authorizations/captures, transfers/refunds, fees and Stripe references. Selecting an operation answers why it happened.

A customer/driver view switch can demonstrate receipt confirmation. Simulation controls are separate from an agent's authority: moving a marker supplies evidence, not permission to bypass a rule. Clearly label map positions, insurance records, merchant events and receipt confirmations as simulated. Stripe operations use actual test APIs.

## Policy examples

These are illustrative configurable demo rules, not legal insurance terms or a production policy.

| Clause | Rule | Enforcement/evidence |
|---|---|---|
| DRIVER-1 | Only an assigned, approved driver may claim a job or its fuel allowance | Assignment version and driver identity/session |
| COVER-1 | Supplied coverage record must include job type, vehicle, goods value and every planned zone, and remain valid through expected completion | Versioned demo insurer record; no real insurance-provider integration claimed |
| TRAVEL-1 | Route must remain within the organisation's permitted zones | Route-zone intersection and current simulated position; eligible cover never overrides a prohibited zone |
| FUEL-1 | Active eligible job may receive up to GBP 8 at permitted fuel merchants; no spend while unassigned or after completion | Issuing category/country controls plus local job policy and budget reservation |
| FUEL-2 | Fuel stop must lie within a configured distance of the approved route, with fresh position evidence | Map geometry and timestamp; Stripe country controls alone do not supply a local geofence |
| RECEIPT-1 | Delivery release requires correct assigned driver, fresh position inside destination radius and fresh one-use recipient confirmation bound to the delivery | Versioned assignment, simulation timestamp, distance, receipt event ID and consumed nonce |
| PAY-1 | Pay approved merchant bill and driver fee once after delivery proof passes, within limits | Original order, approved amounts, current policy, settlement operation IDs and ledger |
| APPROVE-1 | Amounts above the written automatic limit require human approval for that operation | Approval bound to amount, currency, recipient and policy version; no invoice splitting |
| REFUND-1 | A canceled order before pickup receives a full refund if no recipient transfers have executed | Original test charge, order/custody state and remaining refundable amount |

Fuel entitlement is a cost cap; it is not automatically a bank transfer to the driver. The buyer worker issues a virtual card and demonstrates authorization/capture. Merchant-category controls restrict the merchant category, not the contents of every basket. App-level route checks and global budget accounting remain necessary. [Issuing controls](https://docs.stripe.com/issuing/controls/spending-controls)

## Four scenarios

### 1. Deliver and release

Collect a customer test payment, approve driver/route/coverage, then animate travel. An early receipt attempt outside the destination geofence fails even with a valid receipt code. On arrival, a new valid confirmation passes and two distinct Connect transfers pay merchant and driver. Keep gross platform retention and Stripe fees visible. Failure of one transfer leaves the other confirmed; retry/reconcile only the unfinished operation.

### 2. Fuel entitlement

An active eligible driver reaches an approved fuel stop. Demonstrate a GBP 5 fuel-merchant authorization/capture within the GBP 8 allowance. Decline an over-limit or non-permitted-merchant attempt. Inactive/completed jobs receive no new entitlement. The issued card and merchant interaction are sandbox simulations; the capture produces a Stripe test Issuing Transaction.

### 3. Coverage or travel change causes replanning

Before pickup, a route change makes the proposed trip incompatible with Driver A's supplied coverage. Policy refuses dispatch and any unused fuel authority for that assignment. TaskEngine evaluates another route/eligible Driver B, recomputes the allowance and replaces only pending work. If every candidate breaches policy, escalate or cancel; never weaken the requirement to force success. No after-pickup reassignment is claimed without an explicit custody-handover flow.

### 4. Cancel and refund

A separate order has no eligible driver/route before pickup. The agent cancels pending work and refunds the original customer payment under REFUND-1. Check external state before releasing uncertain reservations or retrying payment operations. Refunds after transfers require a separately specified recovery policy; do not imply refunding a charge automatically reverses transfers.

## Actual financial routing

Customer payment method -> platform Stripe test Payments balance -> separate Connect transfers to merchant and driver test accounts. Use a distinct funded Issuing test balance for the fuel purchase; do not assume the customer's charge automatically funds Issuing. A canceled order refunds its original payment method.

Stripe documents separate charges/transfers for restaurant-and-driver-style splits. Configure eligible connected accounts and prove test balance availability before recording. A Transfer credits a Stripe account; an external-bank Payout is a further optional step. [Connect transfers](https://docs.stripe.com/api/transfers/create), [separate charges/transfers](https://docs.stripe.com/connect/separate-charges-and-transfers), [Issuing testing](https://docs.stripe.com/issuing/testing?testing-method=with-code), [refunds](https://docs.stripe.com/api/refunds/create)

Do not call this escrow: Stripe documents controlled payout timing, not an escrow service. [Manual payouts](https://docs.stripe.com/connect/manual-payouts)

## Authentication and evidence

Treat identity authentication, payment-card authentication and delivery authorisation as separate decisions. Stripe 3DS can authenticate the paying cardholder at checkout. The map and recipient-confirmation workflow supplies delivery-policy evidence. GPS/geolocation does not replace an authentication factor. [Stripe 3DS](https://docs.stripe.com/payments/3d-secure), [NIST assurance levels](https://pages.nist.gov/800-63-4/sp800-63b/aal/)

The simulator demonstrates these controls without claiming tamper-proof physical presence or production MFA. Receipt codes/nonces expire, are rate-limited and are bound to order, recipient and assignment; replaying them never authorises another payment. A production identity or insurer integration is outside the first demo.

## TaskEngine and Cognee responsibilities

- Dispatch/planning worker proposes driver, route and dependencies.
- Coverage/travel policy worker performs deterministic eligibility checks against current approved policy and supplied evidence.
- Card buyer worker issues/restricts cards; its real-time authorization path reads prepared state and avoids LLM/Cognee latency.
- Bill-payment and driver-payment workers execute narrowly scoped authorised transfers.
- Receipt worker validates evidence and emits an idempotent delivery-confirmed event.
- Refund worker evaluates cancellation policy and executes bounded refunds.
- Memory worker stores policy versions and structured decisions in Cognee and retrieves history for explanation. Keep the canonical ledger/policy snapshot outside semantic retrieval.

The planner receives semantic failures (coverage mismatch, prohibited zone, proof missing, approval required, payment outcome unknown). It may change route, driver or future tasks while preserving policy and confirmed money effects. Every authorisation includes operation, recipient, amount/currency, policy and assignment versions. A policy/assignment change invalidates unused authority. Persist stable operation IDs and reconcile Stripe state before fallback payment paths.

## Verification targets

Policy boundary and concurrency checks should cover destination radius, stale location, expired/reused receipt, wrong assignment, invalid coverage, prohibited route, fuel cap/category, aggregate budget, changed policy, duplicate events, uncertain Stripe outcomes and a partially completed merchant/driver split. Verify real test PaymentIntent, captured Issuing Transaction, merchant/driver Transfers and Refund with object IDs and livemode=false. Label pending/available funds accurately.

## Judging and two-minute presentation

The [submission page](https://hackathons.briefcase.so/stripe-october-2026/submit/) requires a public GitHub repository and a video no longer than two minutes. The [main brief](https://hackathons.briefcase.so/stripe-october-2026/) assigns 35 points to end-to-end operation, 25 to autonomy/control, 20 to explanations and 20 to product/creativity.

Suggested recording: 0-15s introduce map, order and written rules; 15-40s show a coverage failure and automatic replan; 40-65s show policy-controlled fuel capture; 65-95s reject early receipt then release merchant/driver transfers on valid receipt; 95-110s show a second canceled order's refund; 110-120s open why-paid evidence and Stripe references. Rehearsal must establish whether this fits; simplify animation before dropping financial evidence.

## Before implementation

Confirm sender/merchant meaning and the proposed scenarios; verify Stripe Connect and Issuing sandbox access; choose exact amounts, limits and geometry; select TaskEngine runtime/dependency revision. Insurance records and map/receipt events remain labelled simulations. This document is a concrete draft awaiting design approval.
