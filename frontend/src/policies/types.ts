/** Wire types for src/treasury/routes_policies.py (snake_case, no envelope). Money is integer minor units. */
export type TargetType = 'organisation' | 'mission' | 'card' | 'payment'
export type Outcome = 'allow' | 'hold' | 'deny'

export interface WireClause {
  id: string
  prose: string
  kind: string
  params: Record<string, unknown>
}

export interface PolicyBundle {
  policy_id: string
  version: number
  title: string
  content_hash: string
  clauses: WireClause[]
  memory?: { ok: boolean; status: string; message: string; source_ref: string | null }
}

export interface Attachment {
  attachment_id: string
  target_type: TargetType
  target_id: string
  policy_id: string
  policy_version: number
  mandatory: boolean
}

export interface TargetRef {
  type: TargetType
  id: string
}

export interface EffectiveTarget {
  target_version: number
  attachments: Attachment[]
}

export interface ClauseOutcome {
  policy_id: string
  policy_version?: number
  clause: string | null
  outcome: Outcome
  evidence: Record<string, unknown>
}

export interface DecisionOut {
  verdict: Outcome
  input_revision: number
  effective_policy_hash: string
  attachments: [string, string, number, boolean][]
  results: ClauseOutcome[]
  reasons: string[]
}

export interface DecideInput {
  target: TargetRef
  inputs: Record<string, unknown>
  inputRevision: number
  amountMinor: number
  inherited: TargetRef[]
}

export interface PoliciesClient {
  kind: 'http' | 'fixture'
  listPolicies(): Promise<PolicyBundle[]>
  effective(t: TargetRef): Promise<EffectiveTarget>
  attach(t: TargetRef, policyId: string, version: number, expectedVersion: number): Promise<{ target_version: number }>
  detach(attachmentId: string, expectedVersion: number): Promise<{ target_version: number }>
  decide(d: DecideInput): Promise<DecisionOut>
}

export class PolicyApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'PolicyApiError'
    this.status = status
  }
  get isConflict() {
    return this.status === 409
  }
}
