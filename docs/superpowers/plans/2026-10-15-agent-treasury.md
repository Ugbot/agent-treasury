# Agent Treasury Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development or executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Build policy-controlled Stripe test payments driven by changing inputs, with attached/chained Cognee policies, GOAP planning, DBOS execution, a trigger/graphics frontend and real AI explanations.

**Architecture:** The backend records inputs, resolves immutable effective policy, evaluates deterministic obligations and executes authorised operations through DBOS steps. Basic GOAP chooses compliant next actions; frontend triggers supply labelled simulation inputs and visualise backend state. Cognee supplies policy/evidence memory and a real model supplies read-only chat and local drafts.

**Tech Stack:** Python 3.13, FastAPI, DBOS Python with SQLite, Cognee REST, Stripe test APIs, React/TypeScript/Vite, configurable real-model adapter.

## Global Constraints

- Core is inputs -> attached/chained written policy -> deterministic decision -> payment -> evidence.
- No TaskEngine, LittleHorse or dependency on the old repo.
- Stripe test mode only; reject live keys/objects. Separate Payments and Issuing balances.
- Integer minor units and explicit currencies; persistent operation IDs, reservations, version checks and provider reconciliation.
- Runtime policy graph: acyclic, at most 32 nodes and depth 16; explicit all-of gates; no executable user code.
- Mandatory organisation policy cannot be removed or weakened by attachments.
- GOAP is deterministic/bounded and cannot waive policy; completed money effects survive replans.
- Cognee policy activation must establish source/readiness; model prose never becomes executable financial authority.
- DBOS external steps may run more than once; compensation is explicit and operation-specific.
- Map, insurance and receipt data are labelled simulations. Messages remain local drafts.
- Real integrations are distinct from mocks/unavailable providers. No secrets in Git, browser, memory or model prompts.
- Pin resolved dependency versions and commit locks in the bootstrap tickets; lockfile and official SDK signatures must agree before coding integration.

## Execution method

Read PLAN.md, ARCHITECTURE.md, POLICY_MODEL.md and API_CONTRACT.md first. Work the tracker frontier, not numeric order: frontend begins at ATREAS-14 while backend ATREAS-2 and sandbox spike ATREAS-3 proceed independently. Where a native dependency is not satisfied, implement another ready ticket. This is the planning baseline; implementation is proceeding concurrently and tracker acceptance evidence must establish completion.

Each feature ticket below gives exact file responsibility, boundary interfaces, acceptance targets and planned validation. Tests must be written before the relevant behaviour and fail meaningfully before it is added. Do not write tests that merely restate a rendered implementation. Use transactional/concurrency/restart/provider-boundary tests for financial invariants and browser journeys for the controls.

## First executable test and harness

ATREAS-2 defines the shared test application factory and client fixture:

```python
# tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from treasury.app import create_app

@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "ledger.sqlite"), gateway="mock")
    with TestClient(app) as value:
        yield value
```

```python
# tests/test_core.py

def test_inputs_control_payment(client):
    created = client.post("/api/missions", json={
        "commandId": "mission-1", "scenario": "delivery", "mode": "mock"
    })
    assert created.status_code == 201
    mission_id = created.json()["data"]["missionId"]
    snapshot = client.get("/api/state", params={"missionId": mission_id}).json()["data"]
    assert snapshot["operations"]
    assert not any(op["status"] == "confirmed" for op in snapshot["operations"]
                   if op["kind"] == "transfer")
```

Extend this first test with accepted unique input events and a confirming mock receipt, then repeat the command to assert one logical operation. Deterministic workflow test hooks wait on recorded state/DBOS handles; avoid timing sleeps. ATREAS-11 adds real crash/restart tests in isolated subprocesses and temporary DBs.

## Task 1: ATREAS-2 — Backend: Drive a durable payment decision from changing inputs

**Tracker:** ATREAS-2 · **Stream:** backend · **Blocked by:** None; can start immediately.

**What it delivers:** Run a local mission in a browser: submit changing inputs, inspect allow/hold/deny, and execute an authorised mock payment through a DBOS workflow and persistent ledger. This is the minimal inputs -> policy -> payment vertical slice; mock operation IDs and mode must be explicit.

**Files:**
- Create/modify: `pyproject.toml`
- Create/modify: `src/treasury/models.py`
- Create/modify: `src/treasury/ledger.py`
- Create/modify: `src/treasury/policy.py`
- Create/modify: `src/treasury/workflows.py`
- Create/modify: `src/treasury/api.py`
- Create/modify: `src/treasury/app.py`
- Create/modify: `tests/conftest.py`
- Create/modify: `tests/test_core.py`

**Interfaces:** Consumes strict InputEvent and mission commands; produces create_app(db_path: str, gateway: str), persistent Snapshot/Decision/Operation records, evaluate(payment, inputs, snapshot) -> Decision and mission_workflow(mission_id: str).

**Acceptance:**
- [ ] Browser submits an event with a unique ID and expected input revision; API validates it, persists it and delivers it to a stable DBOS workflow ID.
- [ ] An unmet rule holds the payment; a satisfying input permits it; a forbidden input denies it, each with clause ID, policy version and evidence.
- [ ] Amounts are integer minor units, positive and currency-bound. Duplicate event/start requests create one logical operation and one ledger effect.
- [ ] Restart while awaiting input resumes the existing mission using persistent SQLite. Mock receipts are visibly distinguished from Stripe test receipts.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `tests/test_core.py::test_inputs_control_payment`
- `tests/test_core.py::test_event_and_operation_idempotency`
- `tests/test_recovery.py::test_waiting_mission_resumes`

```sh
uv sync --locked --extra dev
uv run pytest tests/test_core.py tests/test_recovery.py -q
```

**Commit:** `feat: drive a durable payment decision from changing inputs [ATREAS-2]`.

## Task 2: ATREAS-3 — Backend: Prove Stripe sandbox routes and expose capability readiness

**Tracker:** ATREAS-3 · **Stream:** backend · **Blocked by:** None; can start immediately.

**What it delivers:** Run a standalone capability probe against a user-configured Stripe sandbox and record which collection, Connect, Issuing and invoice routes are actually usable. Produce inspectable test receipts and redacted readiness evidence; do not substitute simulated money for missing access.

**Files:**
- Create/modify: `src/treasury/stripe_gateway.py`
- Create/modify: `scripts/check_stripe.py`
- Create/modify: `.env.example`
- Create/modify: `tests/test_stripe_gateway.py`
- Create/modify: `tests/live/test_stripe_routes.py`

**Interfaces:** Consumes ignored sandbox configuration; produces StripeGateway.collect/invoice/transfer/authorize_fuel/capture/refund/reconcile with validated ProviderReceipt values and per-route readiness.

**Acceptance:**
- [ ] Reject live Stripe credentials and any provider object with livemode=true; secret values never enter logs, public files or Cognee.
- [ ] With explicit sandbox configuration, prove customer payment/refund, two distinct eligible Connect recipients and GBP transfer, Issuing authorization/capture, and invoice collection; report each capability separately.
- [ ] Record account context, object IDs, state, currency and funding readiness for Payments and Issuing separately; pending funds are not called available.
- [ ] Without keys or product access, report a specific unavailable capability and setup instruction; no capability is silently marked successful.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `tests/test_stripe_gateway.py::test_rejects_live_mode`
- `tests/live/test_stripe_routes.py::test_sandbox_routes`

```sh
uv sync --locked --extra dev
uv run pytest tests/test_stripe_gateway.py -q
```

**Commit:** `feat: prove stripe sandbox routes and expose capability readiness [ATREAS-3]`.

## Task 3: ATREAS-4 — Backend: Attach versioned Cognee policies to payments and entitlements

**Tracker:** ATREAS-4 · **Stream:** backend · **Blocked by:** ATREAS-2.

**What it delivers:** Create written policy bundles in Cognee and attach exact versions to payments, jobs, cards or counterparties. Inspect the effective rules and evidence on any operation; deterministic evaluation uses a validated immutable snapshot rather than treating semantic search text as executable authority.

**Files:**
- Create/modify: `src/treasury/models.py`
- Create/modify: `src/treasury/policy.py`
- Create/modify: `src/treasury/memory.py`
- Create/modify: `src/treasury/ledger.py`
- Create/modify: `src/treasury/api.py`
- Create/modify: `tests/test_policies.py`
- Create/modify: `tests/test_memory.py`

**Interfaces:** Consumes PolicyAttachment, payment/input snapshots and Cognee configuration; produces effective snapshots, publish_policy/recall_policy/remember_decision, operation-bound approvals and explain records.

**Acceptance:**
- [ ] Create a policy with stable policy/clause IDs, prose, typed rule data, content hash and version; ingest/process it in Cognee and expose memory readiness/source references.
- [ ] Attach or detach optional policies on a pending payment/job/card/counterparty using expected-version concurrency control; mandatory organisation rules cannot be detached or weakened.
- [ ] Effective rules combine mandatory/inherited and explicit attachments restrictively. A permissive attached policy cannot override a denial or bypass an approval/evidence obligation.
- [ ] Every decision/operation records attachment versions, effective policy hash, input revision and clause/evidence results; unknown/stale required policy pauses payment and completed decisions remain immutable.
- [ ] Browser displays attached/effective policies and a why-held/why-paid explanation. Missing Cognee configuration is visible and never represented as a successful memory write.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `tests/test_policies.py::test_effective_attachments_are_restrictive`
- `tests/test_policies.py::test_stale_policy_holds_payment`
- `tests/test_memory.py::test_versioned_policy_roundtrip`

```sh
uv sync --locked --extra dev
uv run pytest tests/test_policies.py tests/test_memory.py -q
```

**Commit:** `feat: attach versioned cognee policies to payments and entitlements [ATREAS-4]`.

## Task 4: ATREAS-5 — Backend: Add and chain policies live without bypassing payment gates

**Tracker:** ATREAS-5 · **Stream:** backend · **Blocked by:** ATREAS-4.

**What it delivers:** Use a simple browser policy composer to add typed conditions and obligations, chain them, validate a version, preview its effect and activate it for pending actions. GOAP can fulfil outstanding obligations but cannot reinterpret or remove them.

**Files:**
- Create/modify: `src/treasury/policy_graph.py`
- Create/modify: `src/treasury/policy.py`
- Create/modify: `src/treasury/api.py`
- Create/modify: `tests/test_policy_graph.py`

**Interfaces:** Consumes typed policy drafts/attachments; produces validate_graph(draft), evaluate_graph(graph, inputs) -> Decision and version-checked preview/activate API outcomes.

**Acceptance:**
- [ ] Composer supports amount thresholds, insurance coverage, travel zone, fuel allowance, recipient confirmation and human approval nodes with explicit all-of joins and typed actions.
- [ ] Reject cycles, unknown operators/effects, invalid units and incompatible action scopes; bound the graph to 32 nodes and 16 depth; never execute user code.
- [ ] An example chain amount > GBP100 -> issue invoice -> payment confirmed -> coverage -> receipt -> release is inspectable, versioned and evaluated deterministically.
- [ ] Activating or attaching a new version invalidates unused authority, re-evaluates pending operations and preserves confirmed Stripe effects/history; concurrent stale edits return conflict.
- [ ] Show before/after decision diff, unmet obligations and exact clause path; replaying the editor or its preview cannot create provider payments.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `tests/test_policy_graph.py::test_chain_requires_all_obligations`
- `tests/test_policy_graph.py::test_rejects_cycles_and_bad_types`
- `tests/test_policy_graph.py::test_activation_invalidates_unused_authority`

```sh
uv sync --locked --extra dev
uv run pytest tests/test_policy_graph.py -q
```

**Commit:** `feat: add and chain policies live without bypassing payment gates [ATREAS-5]`.

## Task 5: ATREAS-6 — Backend: Invoice above a threshold and wait for actual payment

**Tracker:** ATREAS-6 · **Stream:** backend · **Blocked by:** ATREAS-3, ATREAS-5.

**What it delivers:** Attach a collection policy that automatically creates a Stripe test invoice above a configured threshold, displays it in the mission and waits for confirmed payment before allowing downstream disbursement. Keep require-invoice-evidence for a bill distinct from issue-invoice-to-collect.

**Files:**
- Create/modify: `src/treasury/stripe_gateway.py`
- Create/modify: `src/treasury/workflows.py`
- Create/modify: `src/treasury/api.py`
- Create/modify: `tests/test_invoice_policy.py`
- Create/modify: `tests/live/test_invoices.py`

**Interfaces:** Consumes collection obligations/threshold decisions; produces exactly one collection strategy, Stripe invoice lifecycle and normalized funding evidence.

**Acceptance:**
- [ ] GBP150 under an invoice-above-GBP100 policy creates one finalized Stripe test invoice automatically; GBP100 follows the ordinary collection path because the condition is strictly greater-than.
- [ ] An issued invoice or hosted payment URL alone never satisfies funded; confirmed paid state and provider payment evidence unlock the next policy obligation.
- [ ] Invoice collection and separate PaymentIntent collection are mutually exclusive for the same obligation; repeated events and workflow retries cannot double bill.
- [ ] Server verifies/deduplicates Stripe events and reconciles current invoice/payment state on uncertain outcomes; browser shows issued, awaiting payment and confirmed states.
- [ ] An existing funded obligation is not billed again when a chain is edited; partial or failed collection cannot be represented as fully funded.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `tests/test_invoice_policy.py::test_threshold_boundary_and_no_double_collection`
- `tests/test_invoice_policy.py::test_unpaid_invoice_blocks_release`
- `tests/live/test_invoices.py::test_test_invoice_paid_unlocks_obligation`

```sh
uv sync --locked --extra dev
uv run pytest tests/test_invoice_policy.py -q
```

**Commit:** `feat: invoice above a threshold and wait for actual payment [ATREAS-6]`.

## Task 6: ATREAS-7 — Backend: Use map and coverage inputs to replan compliant actions with GOAP

**Tracker:** ATREAS-7 · **Stream:** backend · **Blocked by:** ATREAS-5.

**What it delivers:** Show a simulated delivery map where travel, coverage and assignment events change policy facts. A small deterministic GOAP planner chooses a compliant route/driver or reports no plan, exposing old/new plans and the clause that caused replacement.

**Files:**
- Create/modify: `src/treasury/planner.py`
- Create/modify: `src/treasury/scenarios.py`
- Create/modify: `src/treasury/geometry.py`
- Create/modify: `tests/test_goap.py`

**Interfaces:** Consumes recorded world facts, action catalogue and unmet obligations; produces plan(world, goal, actions, max_expansions=1000) -> PlanResult and versioned route/assignment changes.

**Acceptance:**
- [ ] Map emits labelled simulated location/route/coverage events through the same versioned input path; browser animation alone never authorises a financial action.
- [ ] Changing the proposed route before pickup invalidates Driver A coverage; planner selects eligible Driver B or an allowed alternative route and updates pending fuel/payment entitlements.
- [ ] Search uses explicit action preconditions/effects, nonnegative costs, deterministic tie-breaks and finite search bounds; no compliant plan yields hold/escalation/cancel without weakening rules.
- [ ] Organisation travel exclusions apply even if the supplied demo insurance covers the zone; coverage includes job/vehicle/value/zones/expected duration.
- [ ] Completed operations survive replanning, assignment versions change on reassignment, and map replay/scrub generates no new payment commands.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `tests/test_goap.py::test_coverage_change_replans_remaining_actions`
- `tests/test_goap.py::test_no_permitted_plan_does_not_relax_policy`
- `tests/e2e/map.spec.ts::travelInputsControlAuthority`

```sh
uv sync --locked --extra dev
uv run pytest tests/test_goap.py -q
```

**Commit:** `feat: use map and coverage inputs to replan compliant actions with goap [ATREAS-7]`.

## Task 7: ATREAS-8 — Backend: Release merchant and driver payments only on valid proof

**Tracker:** ATREAS-8 · **Stream:** backend · **Blocked by:** ATREAS-3, ATREAS-5, ATREAS-7.

**What it delivers:** Collect a customer payment, attach settlement policies and use assigned-driver identity, current destination position and one-use recipient confirmation to release merchant and driver Stripe test transfers. Present the sender as merchant by working assumption.

**Files:**
- Create/modify: `src/treasury/proof.py`
- Create/modify: `src/treasury/workflows.py`
- Create/modify: `src/treasury/stripe_gateway.py`
- Create/modify: `tests/test_delivery_proof.py`
- Create/modify: `tests/test_settlement.py`
- Create/modify: `tests/live/test_settlement.py`

**Interfaces:** Consumes funded/coverage/assignment/receipt/approval evidence; produces validated release decisions and independently recorded merchant/driver transfer operations.

**Acceptance:**
- [ ] Seed a GBP100 order split GBP70 merchant, GBP20 driver, GBP10 gross platform retention before Stripe fees; show fees/net balance effects and any fuel operating cost separately.
- [ ] Outside destination radius, stale position, wrong assignment or reused/expired receipt holds or denies release even if other evidence passes; all gates must pass.
- [ ] An amount above the configured automatic transfer limit waits for operation-bound approval; changes to amount/recipient/policy invalidate the approval.
- [ ] On valid proof create two distinct Connect transfers with stable per-operation idempotency keys and recorded policy/input/evidence references; no premature transfer.
- [ ] One successful transfer and one failure is recorded as partial settlement; never call this atomic settlement or a completed bank payout, and never repay the successful leg.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `tests/test_delivery_proof.py::test_all_release_gates_required`
- `tests/test_settlement.py::test_partial_split_does_not_repay_success`
- `tests/live/test_settlement.py::test_two_test_transfers_after_receipt`

```sh
uv sync --locked --extra dev
uv run pytest tests/test_delivery_proof.py tests/test_settlement.py -q
```

**Commit:** `feat: release merchant and driver payments only on valid proof [ATREAS-8]`.

## Task 8: ATREAS-9 — Backend: Issue a trip fuel allowance and enforce it through Issuing

**Tracker:** ATREAS-9 · **Stream:** backend · **Blocked by:** ATREAS-3, ATREAS-5, ATREAS-7.

**What it delivers:** Attach travel/insurance/fuel policies to a trip card. An eligible active driver at a permitted fuel stop receives capped spending authority; demonstrate one captured fuel purchase and denied invalid attempts in Stripe Issuing test mode.

**Files:**
- Create/modify: `src/treasury/fuel.py`
- Create/modify: `src/treasury/workflows.py`
- Create/modify: `src/treasury/stripe_gateway.py`
- Create/modify: `tests/test_fuel_policy.py`
- Create/modify: `tests/live/test_issuing.py`

**Interfaces:** Consumes active trip entitlement/effective policy and fuel_attempt events; produces two-second deterministic authorization decisions, reservations and captured Issuing evidence.

**Acceptance:**
- [ ] Eligible active assignment may spend up to GBP8 at configured fuel categories/countries and permitted simulated route stops; GBP5 authorization/capture succeeds.
- [ ] Over-limit, non-permitted merchant category, out-of-route, stale input, invalid coverage and inactive/reassigned/completed job attempts are rejected.
- [ ] Card is backed by separately funded Issuing test balance; creation alone is not called spending, and capture is linked to its test Issuing Transaction.
- [ ] Prepare deterministic authorization state outside the two-second webhook path, maintain atomic local reservations and validate provider signatures; no LLM/Cognee call blocks the authorization response.
- [ ] Deactivation/reassignment revokes unused authority while outstanding authorizations/captures remain reconciled; labels explain test merchant simulation and merchant-category versus basket contents.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `tests/test_fuel_policy.py::test_trip_fuel_entitlement_boundaries`
- `tests/test_fuel_policy.py::test_concurrent_attempts_share_cap`
- `tests/live/test_issuing.py::test_capture_and_policy_declines`

```sh
uv sync --locked --extra dev
uv run pytest tests/test_fuel_policy.py -q
```

**Commit:** `feat: issue a trip fuel allowance and enforce it through issuing [ATREAS-9]`.

## Task 9: ATREAS-10 — Backend: Refund a policy-approved canceled delivery without duplicate money movement

**Tracker:** ATREAS-10 · **Stream:** backend · **Blocked by:** ATREAS-3, ATREAS-5, ATREAS-7.

**What it delivers:** Cancel a separate funded order before pickup when no eligible driver or route exists. The refund agent evaluates its attached policy, refunds the original payment and explains the decision in the map/ledger.

**Files:**
- Create/modify: `src/treasury/refunds.py`
- Create/modify: `src/treasury/workflows.py`
- Create/modify: `src/treasury/stripe_gateway.py`
- Create/modify: `tests/test_refunds.py`
- Create/modify: `tests/live/test_refunds.py`

**Interfaces:** Consumes original funding, custody/cancel/transfer history and refund policy; produces one bounded original-payment refund operation and its provider status.

**Acceptance:**
- [ ] A pre-pickup canceled GBP100 order with no merchant/driver transfers receives one full refund to its original payment method, capped by remaining refundable value.
- [ ] Already delivered, custody-ambiguous, already refunded or uncertain-outcome orders do not receive a blind automatic refund; they follow hold/escalation.
- [ ] Refund state and provider references appear with exact policy clause, original collection and cancel evidence; duplicated cancel inputs create one semantic refund.
- [ ] Refunds do not automatically restore spending allowance or reverse separate Connect transfers; any captured fuel cost and processing fees remain explicit.
- [ ] Browser shows refund submitted/pending/confirmed/failed accurately and does not declare success at request dispatch.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `tests/test_refunds.py::test_canceled_before_pickup_refunds_once`
- `tests/test_refunds.py::test_unknown_or_settled_order_holds_refund`
- `tests/live/test_refunds.py::test_original_test_payment_refunded`

```sh
uv sync --locked --extra dev
uv run pytest tests/test_refunds.py -q
```

**Commit:** `feat: refund a policy-approved canceled delivery without duplicate money movement [ATREAS-10]`.

## Task 10: ATREAS-11 — Backend: Recover and reconcile payment sagas across crashes and partial failures

**Tracker:** ATREAS-11 · **Stream:** backend · **Blocked by:** ATREAS-6, ATREAS-8, ATREAS-9, ATREAS-10.

**What it delivers:** Interrupt payment workflows at meaningful fault points and recover them with DBOS. Explicit compensating steps cancel unused authority or request permitted reversals/refunds; the ledger exposes unresolved compensation instead of declaring success.

**Files:**
- Create/modify: `src/treasury/workflows.py`
- Create/modify: `src/treasury/ledger.py`
- Create/modify: `src/treasury/stripe_gateway.py`
- Create/modify: `tests/test_recovery.py`
- Create/modify: `tests/test_sagas.py`

**Interfaces:** Consumes persistent forward operations and provider reconcile results; produces durable recovery/compensation branches with stable identities and unresolved-compensation status.

**Acceptance:**
- [ ] Crash after provider success but before DBOS checkpoint resumes using the same semantic operation/idempotency key and reconciliation; no duplicate invoice, transfer, capture or refund.
- [ ] Record intended operation and reserve authority atomically before external I/O; each operation and compensation has a distinct stable identity and validated unchanged parameters.
- [ ] Preserve confirmed split legs across restart/replan; invoke cancellation/refund/reversal only when the provider state and attached compensation policy permit it.
- [ ] An unrecoverable or exhausted compensation stays compensation-required with retry/escalation evidence; financial success is never inferred from an uncaught workflow error.
- [ ] Durable policy/input waits and queued events survive restart; DBOS replay does not re-read non-checkpointed nondeterministic planner inputs.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `tests/test_recovery.py::test_provider_success_before_checkpoint`
- `tests/test_recovery.py::test_partial_settlement_survives_replan`
- `tests/test_sagas.py::test_failed_compensation_is_visible`

```sh
uv sync --locked --extra dev
uv run pytest tests/test_recovery.py tests/test_sagas.py -q
```

**Commit:** `feat: recover and reconcile payment sagas across crashes and partial failures [ATREAS-11]`.

## Task 11: ATREAS-12 — Backend: Use real AI chat to explain policy decisions and draft job messages

**Tracker:** ATREAS-12 · **Stream:** backend · **Blocked by:** ATREAS-4.

**What it delivers:** Add a sidebar backed by a configured real model API. Operators can ask why a payment was held/paid, inspect cited policy evidence and generate editable messages for the driver, merchant or customer; messages stay as local drafts.

**Files:**
- Create/modify: `src/treasury/chat.py`
- Create/modify: `src/treasury/memory.py`
- Create/modify: `src/treasury/api.py`
- Create/modify: `tests/test_chat.py`

**Interfaces:** Consumes persisted context, Cognee evidence, history IDs and real model configuration; produces ChatReply/Draft with validated source citations and no mutation tools.

**Acceptance:**
- [ ] Actual provider call uses selected job facts, decision records and retrieved Cognee policy evidence with bounded history/tokens/timeout; provider and model are visible.
- [ ] Why-paid/held responses cite only known policy version/clause and evidence IDs, link them to the ledger and state uncertainty when supporting evidence is absent.
- [ ] Generate recipient-specific editable drafts without sending externally; generation failure and unavailable keys are shown rather than replaced with canned text labelled AI.
- [ ] Chat has no payment or policy-activation authority; instruction-like text from invoices, messages or memory cannot override executable policy.
- [ ] Use text-safe rendering, redact secrets/card data and avoid exposing precise real user location; persist draft/message IDs and generation provenance.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `tests/test_chat.py::test_grounded_context_and_no_write_tools`
- `tests/test_chat.py::test_missing_provider_is_explicit`
- `tests/e2e/chat.spec.ts::draftsRemainLocal`

```sh
uv sync --locked --extra dev
uv run pytest tests/test_chat.py -q
```

**Commit:** `feat: use real ai chat to explain policy decisions and draft job messages [ATREAS-12]`.

## Task 12: ATREAS-13 — Rehearse the integrated demo and publish the OSS submission

**Tracker:** ATREAS-13 · **Stream:** release · **Blocked by:** ATREAS-6, ATREAS-8, ATREAS-9, ATREAS-10, ATREAS-11, ATREAS-12, ATREAS-19.

**What it delivers:** Polish the policy-first web console, rehearse the four scenarios plus live policy attachment/chaining, and package a reproducible public MIT-licensed repo with a <=2-minute Stripe test-mode demo video.

**Files:**
- Create/modify: `README.md`
- Create/modify: `scripts/check_demo.py`
- Create/modify: `docs/SCENARIOS.md`
- Create/modify: `tests/integration/test_full_demo.py`
- Create/modify: `.github/workflows/checks.yml`

**Interfaces:** Consumes all integrated tests and provider evidence; produces reproducible repo, actual public URLs, recording and completed submission checklist.

**Acceptance:**
- [ ] Browser visibly connects input changes, attached/chained rules, deterministic decisions, GOAP plan changes, DBOS execution status and real Stripe test ledger objects.
- [ ] Exercise delivery release, fuel authorization/capture, coverage/route replan, cancellation refund, invoice threshold and a grounded AI draft; select a legible recording sequence <=120 seconds.
- [ ] Fresh-clone instructions, pinned dependency locks, example configuration without credentials, written policies and automated checks reproduce the app; real integration claims include evidence.
- [ ] Verify no live keys/PII/private receipts in staged/public artifacts, run appropriate dependency audits, and keep map/coverage/receipt simulation labels visible.
- [ ] Create/push the public GitHub repo, record an accessible video and prepare required submission fields. Publishing/submitting uses the user-authorised destination and confirms resulting public URLs; do not mark done before external artifacts exist.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `tests/e2e/demo.spec.ts::policyDrivenPaymentsJourney`
- `tests/integration/test_full_demo.py::test_all_financial_routes`
- `manual::fresh_clone_and_public_submission`

```sh
uv sync --locked --extra dev
uv run pytest tests/integration/test_full_demo.py -q
```

**Commit:** `docs: rehearse the integrated demo and publish the oss submission [ATREAS-13]`.

## Task 13: ATREAS-14 — Frontend: build the trigger deck and input-to-payment timeline

**Tracker:** ATREAS-14 · **Stream:** frontend · **Blocked by:** None; can start immediately.

**What it delivers:** Create the React/TypeScript simulator shell with a trigger deck and graphics showing input -> attached policy -> decision -> payment. This is the frontend foundation and owns shared presentation types/client adapters against docs/API_CONTRACT.md. Use explicit fixture mode until backend integration.

**Files:**
- Create/modify: `frontend/package.json`
- Create/modify: `frontend/src/App.tsx`
- Create/modify: `frontend/src/api.ts`
- Create/modify: `frontend/src/types.ts`
- Create/modify: `frontend/src/components/TriggerDeck.tsx`
- Create/modify: `frontend/src/components/SagaTimeline.tsx`

**Interfaces:** Consumes API_CONTRACT.md; produces shared typed client, shell, input controls and fixture/live adapters used by every frontend component.

**Acceptance:**
- [ ] Controls emit typed, uniquely identified commands for amount, insurance, location, receipt, policy attachment and scenario selection; backend decides authority.
- [ ] Display current inputs, policy version, allow/hold/deny, pending obligations and a chronological event feed with distinct simulation/provider modes.
- [ ] Create responsive accessible shell, loading/error/disabled states, keyboard controls and a frontend test harness; no secrets or payment authority in the browser.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `frontend/src/__tests__/triggers.test.tsx::typedInputCommands`
- `frontend/e2e/console.spec.ts::inputDecisionTimeline`

```sh
cd frontend
npm ci --ignore-scripts
npm run test -- --run
npm run typecheck
npm run build
```

**Commit:** `feat: frontend: build the trigger deck and input-to-payment timeline [ATREAS-14]`.

## Task 14: ATREAS-15 — Frontend: draw chained policies and attach them to payment cards

**Tracker:** ATREAS-15 · **Stream:** frontend · **Blocked by:** ATREAS-14.

**What it delivers:** Build the policy composer and attachment UI: select a payment/job/card, add or chain rules, preview which clauses pass, and activate a validated new version. Policy paths and unmet obligations are visible graphics rather than hidden JSON.

**Files:**
- Create/modify: `frontend/src/components/PolicyGraph.tsx`
- Create/modify: `frontend/src/components/PolicyAttachments.tsx`
- Create/modify: `frontend/src/__tests__/policy-graph.test.tsx`

**Interfaces:** Consumes shared client/types; produces graph/attachment editor components emitting only typed preview/activation/attachment commands.

**Acceptance:**
- [ ] Amount/invoice, insurance, travel, fuel, receipt and approval nodes can be added and connected; render all-of joins, versions, inherited mandatory rules and payment attachments.
- [ ] Show server validation, cycles/type errors, before/after impact and stale-edit conflict. Preview never sends payment/activation commands.
- [ ] Editing a pending payment displays invalidated unused authority; completed payments retain the original rule/evidence inspector. Component fixtures conform to the shared contract.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `frontend/src/__tests__/policy-graph.test.tsx::chainAndAttach`
- `frontend/e2e/policies.spec.ts::previewAndActivate`

```sh
cd frontend
npm ci --ignore-scripts
npm run test -- --run
npm run typecheck
npm run build
```

**Commit:** `feat: frontend: draw chained policies and attach them to payment cards [ATREAS-15]`.

## Task 15: ATREAS-16 — Frontend: simulate travel with map triggers and policy overlays

**Tracker:** ATREAS-16 · **Stream:** frontend · **Blocked by:** ATREAS-14.

**What it delivers:** Build the simulated map and movement controls as an input instrument. Draw drivers, merchant, destination, geofences, fuel stops, insurance/allowed zones, and old/replanned routes; animate movement and let the operator trigger route/coverage changes.

**Files:**
- Create/modify: `frontend/src/components/DeliveryMap.tsx`
- Create/modify: `frontend/src/__tests__/map.test.tsx`

**Interfaces:** Consumes Snapshot.map, inputs and plan; produces geometry graphics and canonical travel events; read-only replay emits no commands.

**Acceptance:**
- [ ] Run/pause and step controls emit canonical position events; route/coverage triggers show current eligibility, clause failures and a replacement plan from backend state.
- [ ] Geofence, fuel-stop corridor, assigned driver and coverage layers have legends and accessible text equivalents; simulated coordinates/evidence are labelled.
- [ ] Scrubbing/replaying changes the historical view only, emits no new financial commands, and cannot present fixture effects as Stripe transactions.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `frontend/src/__tests__/map.test.tsx::movementEventsAndReplay`
- `frontend/e2e/map.spec.ts::travelInputsControlAuthority`

```sh
cd frontend
npm ci --ignore-scripts
npm run test -- --run
npm run typecheck
npm run build
```

**Commit:** `feat: frontend: simulate travel with map triggers and policy overlays [ATREAS-16]`.

## Task 16: ATREAS-17 — Frontend: visualise money flows, allowances and saga evidence

**Tracker:** ATREAS-17 · **Stream:** frontend · **Blocked by:** ATREAS-14.

**What it delivers:** Create payment/entitlement graphics showing customer collection or invoice, merchant/driver split, fuel-card allowance/capture, refund path and DBOS saga progress. Selecting an object opens why-paid/held evidence.

**Files:**
- Create/modify: `frontend/src/components/MoneyFlow.tsx`
- Create/modify: `frontend/src/components/EvidencePanel.tsx`
- Create/modify: `frontend/src/__tests__/money-flow.test.tsx`

**Interfaces:** Consumes Snapshot.operations/decisions/events; produces financial state graphics and source-linked evidence inspector.

**Acceptance:**
- [ ] Show minor-unit amounts as GBP, gross retention/fees/net separately, real provider IDs, payment policy attachments and planned/reserved/pending/confirmed/unknown states.
- [ ] Graphically distinguish allowed, held, denied, partially settled and compensation-required flows; card creation, authorization, capture, transfer and bank payout are different states.
- [ ] Inspector links policy/clause version, input snapshot, approvals, provider receipt and plan/saga lineage without exposing secrets; invoice-created never looks like paid.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `frontend/src/__tests__/money-flow.test.tsx::partialAndUnknownStates`
- `frontend/e2e/evidence.spec.ts::inspectEveryPound`

```sh
cd frontend
npm ci --ignore-scripts
npm run test -- --run
npm run typecheck
npm run build
```

**Commit:** `feat: frontend: visualise money flows, allowances and saga evidence [ATREAS-17]`.

## Task 17: ATREAS-18 — Frontend: add the AI chat sidebar and editable message drafts

**Tracker:** ATREAS-18 · **Stream:** frontend · **Blocked by:** ATREAS-14.

**What it delivers:** Build the operator chat sidebar with job-context questions, cited explanations and editable driver/merchant/customer message drafts. Wire the approved chat contract and make real-provider versus unavailable state explicit.

**Files:**
- Create/modify: `frontend/src/components/ChatPanel.tsx`
- Create/modify: `frontend/src/__tests__/chat.test.tsx`

**Interfaces:** Consumes ChatReply/Draft API; produces read-only explanatory chat, recipient draft editing and citation navigation.

**Acceptance:**
- [ ] Ask why a payment is paid/held and navigate returned citations into the policy/evidence inspector; show provider/model and generation status.
- [ ] Generate, edit and copy local recipient-specific drafts; no external send action or policy/payment authority is introduced.
- [ ] Unavailable provider, timeout and missing evidence have visible states; render text safely, bound history and support keyboard/screen reader use.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `frontend/src/__tests__/chat.test.tsx::draftAndCitationLinks`
- `frontend/e2e/chat.spec.ts::draftsRemainLocal`

```sh
cd frontend
npm ci --ignore-scripts
npm run test -- --run
npm run typecheck
npm run build
```

**Commit:** `feat: frontend: add the ai chat sidebar and editable message drafts [ATREAS-18]`.

## Task 18: ATREAS-19 — Frontend: integrate live triggers and record the policy-driven scenarios

**Tracker:** ATREAS-19 · **Stream:** frontend · **Blocked by:** ATREAS-15, ATREAS-16, ATREAS-17, ATREAS-18, ATREAS-6, ATREAS-8, ATREAS-9, ATREAS-10, ATREAS-11, ATREAS-12.

**What it delivers:** Connect all frontend controls and graphics to the real backend, implement the scenario director, and rehearse one legible <=120-second journey demonstrating input-controlled payments, policy attachment/chaining and the four payment examples.

**Files:**
- Create/modify: `frontend/src/components/ScenarioDirector.tsx`
- Create/modify: `frontend/e2e/demo.spec.ts`
- Create/modify: `tests/integration/test_full_demo.py`

**Interfaces:** Consumes all frontend components and actual backend/provider readiness; produces a tested ScenarioDirector and full real-integration journey.

**Acceptance:**
- [ ] Run all controls against actual API commands/state updates; stale versions, duplicate events, approvals, reconnects and provider failures render accurately without optimistic payment success.
- [ ] Scenario director covers valid delivery, early/wrong proof, travel/coverage replan, capped fuel, invoice threshold and cancellation refund; each scenario creates isolated mission IDs without deleting provider history.
- [ ] Browser end-to-end run inspects real Stripe test objects and Cognee policy references, plus a real AI draft; produce responsive polished presentation and a verified <=120-second script.

**Implementation sequence:**

- [ ] Read the linked ticket and contract; pin/check the APIs actually used in this slice.
- [ ] Add the named acceptance test(s), including the rejected/duplicate/unknown state relevant to the slice. Run the narrow suite and observe a meaningful missing-behaviour failure.
- [ ] Implement the specified interfaces and operation/graphic path in the owned files, using the contract and policy rules. Keep external I/O behind adapters and financial execution behind current policy authority.
- [ ] Run the narrow tests below and the relevant type/build checks. For live routes, run the named sandbox test separately with configured access; otherwise record unavailable and keep live acceptance unmet.
- [ ] Update user-facing state/docs, record actual test references/results on the tracker, review the diff for secrets and scope, and commit with this ticket key. Mark done only when its acceptance evidence exists.

**Planned validation:**
- `frontend/e2e/demo.spec.ts::policyDrivenPaymentsJourney`
- `tests/integration/test_full_demo.py::test_all_financial_routes`

```sh
cd frontend
npm ci --ignore-scripts
npm run test -- --run
npm run typecheck
npm run build
```

**Commit:** `feat: frontend: integrate live triggers and record the policy-driven scenarios [ATREAS-19]`.

## Bootstrap and validation details

The first backend ticket creates `pyproject.toml` with Python >=3.13,<3.14, FastAPI, Uvicorn, DBOS, HTTP client, Pydantic and testing dependencies, plus `uv.lock`; use the registry-resolved tested versions. The frontend foundation creates `package.json`, `package-lock.json`, Vite/React/TypeScript, Vitest/Testing Library and Playwright scripts with an explicit package manager version. Avoid arbitrary lifecycle scripts during installation; inspect any required native build before enabling it. These commands become applicable as the owning foundation ticket supplies its manifest and lockfile; inspect the current files because a concurrent workstream has started scaffolding.

The provider adapters have fake transports for unit tests, configured network timeouts, strict response validation and redacted errors. Live tests are opt-in and assert `livemode=false`. Never use missing keys as a passing live test; report skipped/unavailable distinctly. Public readiness remains unmet until live receipts exist.

For backend-local tests use temporary application/system DBs and reset/destroy DBOS between runtimes as documented. No test cleanup touches a user's working data. Browser tests start the app in explicit mock mode except the separately configured real sandbox rehearsal. Record viewport screenshots and inspect labels/overflow/reduced-motion/keyboard paths.

## Saga implementation skeleton

```python
# Logical shape; gateway and ledger functions are the interfaces owned above.
# Register these as DBOS workflow/step functions in their owning tickets.
def settle_two_recipients(operation_ids, run_step, reconcile_step):
    outcomes = []
    for operation_id in operation_ids:
        existing = reconcile_step(operation_id)
        if existing["status"] == "confirmed":
            outcomes.append(existing)
            continue
        if existing["status"] == "unknown":
            return {"status": "held", "reason": "reconciliation_required"}
        outcome = run_step(operation_id)
        outcomes.append(outcome)
        if outcome["status"] != "confirmed":
            return {"status": "partial", "outcomes": outcomes}
    return {"status": "confirmed", "outcomes": outcomes}
```

Compensation gets a separate stable operation ID and written permission; do not add an invented `saga=True` decorator or automatically undo a confirmed recipient because a later leg fails. DBOS synchronous APIs run in synchronous endpoints/worker functions; use async equivalents if adopting asynchronous handlers.

## Plan self-review

The plan covers the four requested payment examples, changing inputs, runtime policy creation/chaining/attachments, invoice thresholds, insurance/fuel/travel rules, GOAP, Cognee, DBOS sagas/recovery, actual AI, explicit frontend triggers/graphics, OSS publication and recording. The dependency graph has no release/integration cycle. Provider capability checks precede real financial slices. Active documents supersede earlier runtime assumptions. No application implementation or test result is claimed by this planning commit.
