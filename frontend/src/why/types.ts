export interface WhyClause {
  policyId: string | null
  version: number | null
  clauseId: string
  prose: string | null
  passed: boolean | null
  outcome: string | null
  observed: string | null
  expected: string | null
  evidence: unknown
}

export interface Explanation {
  paymentId: string
  verdict: string | null
  policy: { id: string | null; version: number | null; hash: string | null }
  clauses: WhyClause[]
  evidence: { label: string; value: string }[]
  stripeIds: string[]
  livemode: boolean | null
  mode: string | null
  /** Where the explanation text was retrieved from. Never claim cognee unless the server said so. */
  source: 'cognee' | 'audit_log' | 'unknown'
  sourceNote: string | null
  text: string | null
  auditSeqs: number[]
}

export interface WhyClient {
  kind: 'http' | 'fixture'
  explain(paymentId: string): Promise<Explanation>
}
