# Agent Treasury: working notes for agents and humans

Policy-controlled payments in Stripe **test mode**. Inputs change; written, versioned, chained policies decide allow / hold / deny; only then does money move; every step is recorded and explainable. Hackathon entry (Briefcase x Stripe, Track 01 "Agents that spend"). Plan: `docs/`, tracker project `ATREAS`.

## Ground rules
- **uv for all Python**: `uv sync`, `uv run pytest -q`, `uv run ruff check src tests`, `uv add <pkg>`. Frontend: `cd frontend && npm test && npm run build`.
- **Stripe test mode only.** Reject `sk_live_`/`rk_live_` keys and any object with `livemode=true`. Test keys may be `sk_test_`, `rk_test_`, `rkcs_test_`.
- **Never read, print or log secrets** (`.env`, keys, webhook secrets). Redact at write time (`audit.redact`, `stripe_gateway.redact`).
- **Money is integer minor units**, and currency is a **policy** (`CURRENCY-1`), never a hard-coded constant. Fallback only via `treasury.currency.DEFAULT_CURRENCY`.
- **Say what is mock.** Every payment/receipt carries `mode` (`mock` | `stripe_test`). Map positions, insurance records and receipts are simulated and labelled so. Missing keys or product access produce an explicit `unavailable` result with setup text, never fake success.
- Do not commit, push or delete unless the human asks. `rm` is blocked on this machine.

## Mental model

```
input event ──▶ policy (chained, versioned) ──▶ decision ──▶ saga/operation ──▶ gateway ──▶ Stripe test
                       │                           │              │
                       └────────── audit log (append-only, hash-chained) ◀──── every step
```

The backend owns authority. The browser, the LLM agent and chat can *propose* or *explain*; they can never pay or override a policy.

## 1. Durable execution (DBOS)
A mission is a long-running, input-driven process: it holds, waits for inputs (receipt, position, approval, invoice paid), then pays. It must survive crashes and restarts without losing its place or paying twice.

- Each mission is one DBOS workflow with the **stable ID `mission:<mission_id>`** (`workflows.py`: `mission_workflow`, `start_mission`, `deliver_event`). Starting twice returns the same workflow.
- **All I/O lives inside `@DBOS.step()`s** (`load_snapshot`, `decide_step`, `reserve_step`, `pay_step`, `compensate_step`). The workflow body itself is deterministic: no clocks, randomness, network or DB reads outside steps, otherwise replay diverges.
- Waiting for input uses DBOS messaging (`recv`/`send`); events are de-duplicated by `event_id`.
- System DB is SQLite (persistent). Restart while waiting resumes the same mission (`tests/test_recovery.py::test_waiting_mission_resumes`).
- Steps must be **safe to re-run**: DBOS gives at-least-once step execution, so every external effect uses a stable operation ID plus an idempotency key.

## 2. Atomic actions
An "action" is the smallest unit that must either fully happen or not happen at all.

- **One SQLite transaction per state change.** Reserving funds and creating the operation row happen together (`SagaStore.reserve`); applying an input event and bumping the mission revision happen together (`Ledger.apply_event`, optimistic concurrency on `expected_revision`, duplicate `event_id` is a no-op).
- **One logical operation = one operation ID = one provider object.** `payment_operation_id(mission_id)` is stable; reusing an ID with different parameters is rejected (`request_hash`). `Ledger.pay_once` / `INSERT OR IGNORE` make the ledger effect idempotent.
- **Audit entries are written in the same transaction as the effect they record** (`AuditLog.append(..., commit=False)` inside the caller's transaction). If the effect rolls back, so does its log line.
- Amounts are positive integers; decisions and completed operations are immutable once written.

## 3. Sagas (multi-step money with explicit undo)
Delivery pays a merchant and a driver, buys fuel, may refund. These are independent legs that can fail separately. Implemented in `saga.py`.

- Operation states: `reserved → submitted → confirmed`, or `unknown` (provider outcome unclear) or `compensation_required`.
- **Reconcile before retry.** After a crash or timeout, `execute()` asks the provider (`gateway.reconcile(operation_id)`) whether the effect already exists before sending again. Never create a second provider object.
- **Legs are independent**: merchant and driver transfers have separate operation IDs; one failing leaves the other confirmed and only the unfinished leg is retried.
- **Compensation is its own operation** with its own stable identity (`comp:<operation_id>`). It refunds only when the provider state is confirmed **and** the policy permits it (`REFUND-1`); otherwise it releases an unsent reservation or leaves `compensation_required` with escalation evidence (`SagaStore.unresolved()`). Refund after transfers needs a recovery policy; a refund does not auto-reverse transfers.
- Gateway errors that mean "unavailable" become an explicit result, never success.

## 4. Dependency-resolving policies
Policies are written, versioned bundles of clauses (`policies.py`, `policy.py`): stable policy and clause IDs, prose, typed rule data (`max_amount`, `require_input`, `forbid_input`, `allowed_currency`), content hash, version.

- **Resolution order.** The effective rule set for a payment = mandatory organisation policies + policies inherited from enclosing targets (organisation → job → card/counterparty → payment) + explicit attachments. `PolicyRegistry.effective_attachments` resolves the chain; `decide` evaluates every clause.
- **Restrictive combination.** `deny > hold > allow`. A permissive attached policy can never override a denial or bypass an approval/evidence obligation. Mandatory org rules cannot be detached, downgraded or weakened; a new version may only tighten (`_weakens` rejects removed clauses, raised limits, widened currencies).
- **Dependencies between obligations.** A payment depends on its prerequisites being satisfied: delivery proof (`PAY-1`), coverage (`COVER-1`), route inside permitted zones (`TRAVEL-1`), approval above the limit (`APPROVE-1`), an invoice actually paid (not merely created). Unmet prerequisites produce **hold with the missing input named**, not a silent failure; satisfying inputs re-run the decision. A forbidden input produces **deny** that stays denied until a new policy version or an explicit, audit-logged override.
- **Pinning.** Attach with expected-version concurrency; a stale or unknown required policy pauses the payment. Every decision records attachment versions, the effective policy hash, input revision, and per-clause outcome + evidence.
- Planner (`planner.py`, GOAP) proposes compliant action sequences and replans when inputs change; it refuses rather than weakening policy. Policy remains the gate.

## 5. Temporal actions (what policy applied *when the action happened*)
Questions we must answer later: "Was this payment allowed by the policy **in force at that moment**?" and "Would it be allowed under today's policy?" Both matter for audit; they can differ.

Already true:
- Policy bundles are immutable per `(policy_id, version)` and carry a content hash; every decision stores the exact policy id, version and hash it used, plus the input revision and evidence (`Decision`, `EffectiveDecision`, `decisions` table, audit `policy` field).
- The audit log is ordered by `seq` with a timestamp and hash chain (`audit.py`, `/audit/verify`), so "what was true at seq N" is reconstructible.

Policy clauses that are time-sensitive (evidence freshness is part of the rule, not a side check):
- position evidence must be fresh (`FUEL-2`, `RECEIPT-1`), receipt nonces expire and are single-use, coverage must remain valid through expected completion (`COVER-1`), fuel entitlement exists only while the job is active.
- Always compare against an **action time supplied by the system clock inside a step or recorded input**, never `now()` in workflow code.

Planned (not implemented yet; mark as such in docs and UI):
- **As-of check.** `check_as_of(action_id, at=<action time>)`: load the policy versions that were published and attached at that time (publish/attach events in the audit log have timestamps), re-evaluate the recorded inputs, and report `allowed_then`. Also `check_now(action_id)` against current versions, and surface divergence ("allowed under v1, would hold under v3 because limit lowered").
- Store `effective_from` for policy versions and attachments so as-of resolution does not depend on log scanning.
- Time-window clauses (`valid_from`/`valid_until`, "within N minutes of arrival") evaluated against the recorded event time.
- Replays must be deterministic: decisions are pure functions of `(policy snapshot, inputs, revision, amount, action_time)`.

## Key files
`src/treasury/`: `policy.py` (pure evaluation) · `policies.py` (registry, attach/chain, effective decision) · `workflows.py` (DBOS) · `saga.py` (operations, reconcile, compensate) · `ledger.py` (SQLite ledger) · `audit.py` (hash-chained log) · `gateway.py`/`stripe_gateway.py` (mock + Stripe test) · `proof.py`, `geo.py` (delivery proof, zones) · `fuel.py`, `refunds.py`, `invoices.py` · `planner.py` · `memory.py` (Cognee adapter) · `chat.py` (explanations only) · `api.py`, `routes_*.py`.

## Testing expectations
- Every policy rule has allow, hold and deny boundary tests; every external effect has duplicate, retry, crash-between-effect-and-checkpoint, and unknown-outcome tests.
- Negative checks that must keep passing: a client cannot clear a deny by resending inputs; strings like `"false"` do not satisfy inputs; lowering an amount does not bypass `APPROVE-1`; an attached stricter policy flips allow to hold; a replayed receipt nonce is rejected; a duplicate event creates one payment.
