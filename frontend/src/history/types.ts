export interface PolicyRef { policyId: string; version: number | null; hash: string; mandatory: boolean }
export interface InForce { effectiveHash: string | null; policies: PolicyRef[]; summary: string | null }
export interface DifferingClause { policyId: string | null; clause: string; then: string | null; now: string | null; why: string | null }
export interface Divergence {
  decisionSeq: number | null
  recordedVerdict: string | null
  allowedThen: boolean
  wouldNow: string | null
  differing: DifferingClause[]
  summary: string
}
export interface HistoryItem {
  seq: number
  kind: string
  category: string
  actor: string
  summary: string
  recordedAt: string
  validAt: string
  validTimeSource: 'explicit' | 'recorded_time'
  lateArrival: boolean
  supersedes: number | null
  inForce: InForce
  verdict: string | null
  reproduced: boolean | null
  clauses: { clause: string; outcome: string | null }[]
  divergence: Divergence | null
}
export interface PaymentHistoryData {
  missionId: string
  requestedId: string
  summary: string
  items: HistoryItem[]
  divergence: Divergence | null
}
export interface PolicyAsOf { at: string; basis: string; effectiveHash: string | null; summary: string | null; policies: PolicyRef[] }
export interface HistoryClient {
  kind: 'http' | 'fixture'
  payment(id: string): Promise<PaymentHistoryData>
  policyAsOf(q: { targetType: string; targetId: string; at: string; basis?: 'valid' | 'recorded' }): Promise<PolicyAsOf>
}
