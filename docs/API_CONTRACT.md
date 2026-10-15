# Frontend/backend contract

This is the planned v1 contract. No endpoints exist yet. The frontend can build fixture views against it; live integration must replace fixtures explicitly. Use camelCase JSON, integer minor units, enum strings and ISO timestamps; simulation time is a separate integer `simTime` in seconds. Validate every external request/response. Monetary enums/IDs are server-owned.

## Read model

`GET /api/state?missionId=<id>&after=<revision>` returns `{data: Snapshot}`. Start with polling every 750ms while a mission is active; add SSE only if needed. `GET /api/health` exposes readiness for DBOS, Stripe routes, Cognee and the real model, with no secrets.

```typescript
type Verdict = 'allow' | 'hold' | 'deny' | 'approval_required';
type PaymentMode = 'mock' | 'stripe_test';
type Scope = 'organisation' | 'mission' | 'payment' | 'card' | 'counterparty';
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
  status: string; mode: PaymentMode; policyAttachments: Attachment[];
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
    memoryRef: string | null; nodes: unknown[]; edges: unknown[]}[];
  operations: Operation[]; decisions: Decision[];
  plan: {planId: string; replaces: string | null; actions: string[];
    reason: string; completedOperationIds: string[]};
  map: {drivers: unknown[]; merchant: unknown; destination: unknown;
    zones: unknown[]; fuelStops: unknown[]; routes: unknown[]};
  events: {eventId: string; type: string; simTime: number;
    description: string; evidenceIds: string[]}[];
}
```

The read-model map/node payloads are versioned typed schemas implemented with the first consumer tickets, not unrestricted execution data. Shared frontend types are generated/checked against FastAPI OpenAPI. `unknown` represents read-only render payloads only; mutating payloads below are strict discriminated schemas.

## Commands and resources

All mutations require demo-operator authority, exact-origin checks, unique command IDs and expected revision where state matters. Return `{data: ...}` with a server revision, or `{error:{code,message,details}}`. Never return stack traces. Use 409 for stale revisions/duplicate ID with changed payload, 422 for invalid schema/graph and 503 for unavailable providers.

| Endpoint | Request | Result |
|---|---|---|
| POST /api/missions | `{commandId, scenario, mode}` | `{missionId, workflowId, revision}`; repeated same command returns same identity |
| POST /api/missions/{id}/events | `{eventId, expectedRevision, type, simTime, payload}` | Accepted event/input revision; DBOS durable send deduplicated by event ID |
| POST /api/policies | `{commandId, policyId, title, scope, clauses, nodes, edges}` | Validated draft version, no authority yet |
| POST /api/policies/{id}/previews | `{version, targetType, targetId, expectedRevision}` | Before/after clause outcomes; no payment side effects |
| POST /api/policies/{id}/activations | `{commandId, version, expectedActiveVersion}` | Memory-ready active snapshot and affected pending decisions |
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
