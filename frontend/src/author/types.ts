import type { WireClause } from '../policies/types'

export type { WireClause }
export type Verdict = 'allow' | 'hold' | 'deny'

export interface AuthorClause extends WireClause {
  /** true while the clause is exactly what the AI drafted (cleared on any human edit). */
  ai?: boolean
}
export interface DraftResult {
  available: boolean
  setup?: string
  draft?: { policy_id: string; title: string; clauses: WireClause[] }
  unsupported: { text: string; reason: string }[]
  model?: string
  ai_drafted: boolean
}
export interface ValidateRequest {
  policy_id: string
  title: string
  clauses: WireClause[]
  expected_version?: number
}
export interface ValidateResult {
  valid: boolean
  errors: { clause: string; message: string }[]
  weakens: string[]
  diff: { added: string[]; removed: string[]; changed: { id: string; before: string; after: string }[] }
  sentences: { id: string; text: string; verdict_if_unmet: Verdict }[]
  mandatory: boolean
  /** Latest published version of this policy id (0 when none). */
  current_version: number
}
export interface DryRunRow {
  situation: string
  label: string
  verdict: Verdict
  clause: string | null
  reason: string
}
export interface PublishRequest extends ValidateRequest {
  expected_version: number
  ai_drafted: boolean
  prompt?: string
  model?: string
}
export interface PublishedBundle {
  policy_id: string
  version: number
  title: string
  content_hash: string
}
export interface AuthorClient {
  kind: 'http' | 'fixture'
  kinds(): Promise<string[]>
  draft(description: string, basePolicyId?: string): Promise<DraftResult>
  validate(req: ValidateRequest): Promise<ValidateResult>
  dryRun(clauses: WireClause[]): Promise<DryRunRow[]>
  publish(req: PublishRequest): Promise<PublishedBundle>
}
