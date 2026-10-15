/** Shared frontend types for docs/API_CONTRACT.md. Money is integer minor units. */
export type Verdict = 'allow' | 'hold' | 'deny' | 'approval_required'
/** fixture = frontend canned data, mock = backend simulation, stripe_test = Stripe test mode. */
export type DataMode = 'fixture' | 'mock' | 'stripe_test'
export type EventSource = 'simulation' | 'provider'
export type Scope = 'organisation' | 'mission' | 'payment' | 'card' | 'counterparty'

export interface Inputs {
  amountMinor: number
  currency: string
  insured: boolean
  location: string
  receiptConfirmed: boolean
}

export interface PolicyRef {
  policyId: string
  version: number
  hash: string
  mandatory: boolean
}

export interface ClauseResult {
  clauseId: string
  prose: string
  passed: boolean
  observed: string
  expected: string
}

export interface Decision {
  decisionId: string
  verdict: Verdict
  inputRevision: number
  effectivePolicyHash: string
  clauses: ClauseResult[]
  obligations: { obligationId: string; kind: string; status: string }[]
}

export interface PaymentOperation {
  operationId: string
  kind: string
  amountMinor: number
  currency: string
  status: string
  mode: DataMode
  providerObjectId: string | null
}

export interface FeedEvent {
  eventId: string
  type: string
  simTime: number
  description: string
  mode: DataMode
  source: EventSource
}

export interface Snapshot {
  revision: number
  mode: DataMode
  assignmentVersion?: number
  missionId: string
  scenario: string
  inputs: Inputs
  policies: PolicyRef[]
  decision: Decision | null
  operation: PaymentOperation | null
  events: FeedEvent[]
}

export interface PolicyOption {
  policyId: string
  version: number
  title: string
}

export interface Scenario {
  id: string
  title: string
}

export interface CommandEnvelope<K extends string, P> {
  commandId: string
  type: K
  missionId: string
  expectedRevision: number
  issuedAt: string
  payload: P
}

export type Command =
  | CommandEnvelope<'set_amount', { amountMinor: number; currency: string }>
  | CommandEnvelope<'set_insurance', { covered: boolean }>
  | CommandEnvelope<'set_location', { location: string }>
  | CommandEnvelope<'confirm_receipt', { recipientId: string; receiptNonce: string }>
  | CommandEnvelope<'attach_policy', { targetType: Scope; targetId: string; policyId: string; policyVersion: number }>
  | CommandEnvelope<'report_position', { driverId: string; x: number; y: number; assignmentVersion: number }>
  | CommandEnvelope<'cancel_requested', { reason: string }>
  | CommandEnvelope<'select_scenario', { scenario: string; mode: DataMode }>

export type CommandType = Command['type']

export interface CommandResult {
  accepted: boolean
  revision: number
  commandId: string
}

export interface ApiErrorBody {
  code: string
  message: string
  details?: unknown
}
