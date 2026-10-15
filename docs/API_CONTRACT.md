# Frontend/backend contract

This is the target v1 contract. Implementation is proceeding concurrently; compare current OpenAPI and shared types before marking integration accepted. The frontend can build fixture views against it; live integration must replace fixtures explicitly. Use camelCase JSON, integer minor units, enum strings and ISO timestamps; simulation time is a separate integer `simTime` in seconds. Validate every external request/response. Monetary enums/IDs are server-owned.

## Read model

`GET /api/state?missionId=<id>&after=<revision>` returns `{data: Snapshot}`. Start with polling every 750ms while a mission is active; add SSE only if needed. `GET /api/health` exposes readiness for DBOS, Stripe routes, Cognee and the real model, with no secrets.

```typescript
type Verdict = 'allow' | 'hold' | 'deny' | 'approval_required';
type PaymentMode = 'mock' | 'stripe_test';
type Scope = 'organisation' | 'mission' | 'payment' | 'card' | 'counterparty';
type OperationStatus = 'planned' | 'reserved' | 'submitted' | 'confirmed' |
  'held' | 'denied' | 'awaiting_approval' | 'unknown' | 'failed' |
  'compensating' | 'compensated' | 'compensation_required';
interface Point { x: number; y: number }
interface Driver { driverId: string; name: string; position: Point;
  assignmentVersion: number; eligible: boolean; coverageRecordId: string }
interface Route { routeId: string; points: Point[]; zoneIds: string[];
  status: 'proposed' | 'active' | 'replaced' | 'denied' }
interface MapZone { zoneId: string; label: string; polygon: Point[]; permitted: boolean }
interface MapPlace { placeId: string; label: string; position: Point; radiusMetres: number }
interface PolicyNode { nodeId: string; kind: 'condition' | 'evidence' | 'obligation' | 'all_of';
  clauseId: string; factPath?: string; operator?: 'eq' | 'gt' | 'gte' | 'lte' | 'in' | 'contains_all';
  value?: string | number | boolean | string[]; unit?: 'gbp_minor' | 'metres' | 'seconds';
  verifier?: 'coverage' | 'geofence' | 'route_corridor' | 'receipt' | 'funding' | 'approval';
  obligation?: 'issue_invoice' | 'await_payment' | 'request_approval' |
    'verify_coverage' | 'collect_receipt' | 'reserve_fuel' | 'settle' }
interface Attachment {
  attachmentId: string; targetType: Scope; targetId: string;
  policyId: string; policyVersion: number; mandatory: boolean;
}
interface Decision {
  decisionId: string; verdict: Verdict; operationId: string;
  effectivePolicyHash: string; inputRevision: number;
  clauses: {policyId: string; version: number; clauseId: string;
    passed: boolean; observed: unknown; expected: unknown; evidenceIds: string[]}[];
  obligations: {obligationId: string; kind: string; status: string}[];
}
interface Operation {
  operationId: string; kind: 'collect' | 'invoice' | 'transfer' |
    'fuel_authorization' | 'fuel_capture' | 'refund' | 'reversal';
  amountMinor: number; currency: 'gbp'; recipientId: string;
  status: OperationStatus; mode: PaymentMode; policyAttachments: Attachment[];
  effectivePolicies: Attachment[]; decisionId: string | null;
  provider: {accountId: string; objectType: string; objectId: string;
    livemode: false; status: string} | null;
}
interface Snapshot {
  revision: number; mode: PaymentMode;
  readiness: Record<string, {status: 'ready' | 'unavailable' | 'checking'; reason: string}>;
  mission: {missionId: string; scenario: string; status: string;
    simTime: number; inputRevision: number; assignmentVersion: number} | null;
  inputs: Record<string, unknown>;
  policies: {policyId: string; version: number; title: string; contentHash: string;
    memoryRef: string | null; nodes: PolicyNode[]; edges: {from: string; to: string}[]}[];
  operations: Operation[]; decisions: Decision[];
  plan: {planId: string; replaces: string | null; actions: string[];
    reason: string; completedOperationIds: string[]};
  map: {drivers: Driver[]; merchant: MapPlace; destination: MapPlace;
    zones: MapZone[]; fuelStops: MapPlace[]; routes: Route[]};
  events: {eventId: string; type: string; simTime: number;
    description: string; evidenceIds: string[]}[];
}
```

Shared frontend types are generated/checked against FastAPI OpenAPI. A PolicyNode is a discriminated union in the executable schemas: each kind accepts only its applicable fields. The compact interface above lists those fields together for planning readability; it is not permission to accept mismatched node data. Read-only observed/expected facts may have varied values; mutating payloads are strict discriminated schemas.

## Commands and resources

All mutations require demo-operator authority, exact-origin checks, unique command IDs and expected revision where state matters. First deduplicate an identical command/event before rejecting an old revision, so a retry returns the original accepted result. A reused ID with changed payload is a conflict. Return `{data: ...}` with a server revision, or `{error:{code,message,details}}`. Never return stack traces. Use 409 for stale revisions/duplicate ID with changed payload, 422 for invalid schema/graph and 503 for unavailable providers.

| Endpoint | Request | Result |
|---|---|---|
| POST /api/missions | `{commandId, scenario, mode}` | `{missionId, workflowId, revision}`; repeated same command returns same identity |
| POST /api/missions/{id}/events | `{eventId, expectedRevision, type, simTime, payload}` | Accepted event/input revision; DBOS durable send deduplicated by event ID |
| POST /api/policies | `{commandId, policyId, title, scope, clauses, nodes, edges}` | Validated draft version, no authority yet |
| POST /api/policies/{id}/previews | `{version, targetType, targetId, expectedRevision}` | Before/after clause outcomes; no payment side effects |
| POST /api/policies/{id}/activations | `{commandId, version, expectedActiveVersion}` | Memory-ready active snapshot and affected pending decisions |
| PATCH /api/payments/{id} | `{commandId, amountMinor, currency, expectedRevision}` | Change a planned/held amount only; invalidate unused authority/approval; reject submitted/confirmed operation |
| POST /api/payments/{id}/policy-attachments | `{commandId, policyId, policyVersion, expectedRevision}` | Audited attachment and new effective policy hash |
| DELETE /api/payments/{id}/policy-attachments/{attachmentId} | expectedRevision and commandId headers | Detach optional pending attachment only |
| POST /api/missions/{id}/policy-attachments | Same attachment request | Audited job attachment |
| POST /api/cards/{id}/policy-attachments | Same attachment request | Audited card entitlement attachment |
| POST /api/counterparties/{id}/policy-attachments | Same attachment request | Audited counterparty attachment |
| POST /api/payments/{id}/approvals | `{commandId, decisionId, expectedRevision}` | Operation-bound approval, still subject to execution recheck |
| GET /api/payments/{id}/explanation | none | Structured authorisation/hold record and known citations |
| POST /api/missions/{id}/chat/messages | `{commandId, text, historyIds}` | Actual model reply with citations/provider/model or explicit unavailable |
| POST /api/missions/{id}/drafts | `{commandId, recipientRole, intent}` | Editable local draft; no send |
| PATCH /api/drafts/{id} | `{text, expectedRevision}` | Updated local draft |
| POST /api/webhooks/stripe | Stripe signed raw body | Deduplicated provider event; retrieve current object where required |

Graph node and event envelopes reject unknown properties. No arbitrary expression/operator/function/URL field is executable. Policy/edit endpoints cannot fabricate provider receipt or approval identities.

## Simulator event types

`position` has `{driverId,x,y,assignmentVersion}`; `route_changed` has `{routeId}`; `coverage_changed` has `{driverId,coverageRecordId}`; `receipt_confirmed` has `{recipientId,receiptNonce,assignmentVersion}`; `fuel_attempt` has `{amountMinor,currency,merchantId,category,country,driverId,assignmentVersion}`; `cancel_requested` has `{reason}`; `sim_tick` has `{seconds}`. Amount updates and policy updates use their dedicated typed resource commands. Coordinates and time are bounded finite numbers; monotonic simulator events never reset financial state.

Provider events use the signed webhook path, not a browser `payment_paid` button. A separately named sandbox-test helper may complete a test payment; it is restricted to test mode, invokes Stripe and waits for verified provider state. It cannot manufacture a paid invoice locally.

## AI reply contract

`{messageId,text,provider,model,citations:[{policyId,version,clauseId,evidenceIds,decisionId}],draftId?}`. Server constructs context from persisted facts and Cognee evidence; it validates citation identities before returning them. Chat cannot invoke mutation endpoints. Draft delivery is local only.

## Command envelope (v1, ATREAS-14)

Every mutation from the browser is a typed command. The frontend never decides money authority; it sends inputs and renders what the backend returns.

```typescript
interface CommandEnvelope<K extends string, P> {
  commandId: string;          // client-generated UUID, unique per user action; retries reuse it
  type: K;                    // discriminator
  missionId: string;
  expectedRevision: number;   // last snapshot revision the operator saw
  issuedAt: string;           // ISO timestamp, informational only
  payload: P;
}
```

Command types emitted by the trigger deck: `set_amount {amountMinor, currency}`, `set_insurance {covered: boolean}`, `set_location {x, y, zone}`, `confirm_receipt {recipientId, receiptNonce}`, `attach_policy {targetType, targetId, policyId, policyVersion}`, `select_scenario {scenario, mode}`.

Result: `{data: {accepted: true, revision, commandId}}` or `{error:{code,message,details}}`. A repeated `commandId` with the same payload returns the same result; with a different payload returns 409 `command_conflict`. A stale `expectedRevision` returns 409 `stale_revision` and the client refreshes the snapshot.

Adapter mapping: the frontend client maps each command onto the resource endpoints above (for example `set_amount` to the pending-payment amount proposal on `PATCH /api/payments/{id}`, `attach_policy` to the matching `policy-attachments` route, `select_scenario` to `POST /api/missions`).

## Additional read resources

| Endpoint | Result |
|---|---|
| GET /api/missions/{id} | Mission summary and current revision |
| GET /api/missions/{id}/events?after=<revision> | Chronological event feed, each item carrying `mode` |
| GET /api/missions/{id}/decisions | Decisions with verdict, policy version/hash, clauses and obligations |
| GET /api/policies, GET /api/policies/{id}?version= | Policy versions and content hashes |
| GET /api/missions/{id}/operations | Operations with provider references |
| GET /api/missions/{id}/saga | Saga/evidence trail: ordered steps `{stepId, name, status, operationId, evidenceIds, mode}` |
| POST /api/missions/{id}/chat/messages | See AI reply contract |

## Mode labels

Every event, operation and receipt carries a `mode` the UI must show distinctly:

- `fixture`: frontend-only canned data; no backend involved. Shown only when fixture mode is explicitly enabled (`VITE_API_MODE=fixture`).
- `mock`: backend simulation; no provider call.
- `stripe_test`: Stripe test-mode object (`livemode: false`). Live keys and `livemode: true` objects are rejected by the backend and must never render as success.

Event feed items additionally carry `source: 'simulation' | 'provider'` so simulator inputs are never confused with provider-confirmed events.
