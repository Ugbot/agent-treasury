export type Mode = 'mock' | 'stripe_test'

export interface JobDraft {
  pickup: string
  dropoff: string
  goods_value_minor: number
  price_minor: number
  delivery_fee_minor: number
  deadline: string
  insurance_required: boolean
}

export interface Job extends JobDraft {
  job_id: string
  mission_id: string
  status: 'draft' | 'dispatched'
  currency: string
  total_minor: number
  mode: Mode
  already_dispatched?: boolean
}

export interface LibraryPolicy {
  policy_id: string
  version: number
  title: string
  summary: string
  template: boolean
  mandatory: boolean
  locked: boolean
  parameter: { name: string; label: string; default: number } | null
}

export interface EffectiveRule {
  attachment_id: string
  policy_id: string
  version: number
  title: string
  mandatory: boolean
  locked: boolean
  scope: 'organisation' | 'job'
  clauses: { id: string; prose: string; kind: string; params: Record<string, unknown> }[]
}

export interface Effective {
  job_id: string
  target_version: number
  frozen: boolean
  rules: EffectiveRule[]
}

export interface PreviewResult {
  situation: string
  label: string
  verdict: 'allow' | 'hold' | 'deny'
  clause: string | null
  policy_id: string | null
  reason: string
  amount_minor: number
  simulated: boolean
}

export interface WatchStep {
  seq: number
  ts: string
  kind: string
  actor: string
  party: string | null
  text: string
  mode: Mode
  simulated: boolean
}

export interface Watch {
  job_id: string
  status: 'draft' | 'dispatched' | 'held' | 'approved' | 'denied' | 'paid'
  mode: Mode
  steps: WatchStep[]
  last_seq: number
  done: boolean
}

export interface JourneyClient {
  kind: 'http' | 'fixture'
  library(): Promise<LibraryPolicy[]>
  createJob(d: JobDraft): Promise<Job>
  effective(jobId: string): Promise<Effective>
  attach(jobId: string, policyId: string, expectedVersion: number, params?: Record<string, unknown>): Promise<Effective>
  preview(jobId: string): Promise<PreviewResult[]>
  dispatch(jobId: string): Promise<Job>
  watch(jobId: string): Promise<Watch>
}

export class JourneyApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export const money = (minor: number, currency = 'usd') => {
  const major = Math.trunc(minor / 100).toLocaleString('en-US')
  const cents = String(Math.abs(minor % 100)).padStart(2, '0')
  return currency.toLowerCase() === 'usd' ? `$${major}.${cents}` : `${major}.${cents} ${currency.toUpperCase()}`
}

/** "12.50" -> 1250. Returns null for anything that is not a plain non-negative amount. */
export const toMinor = (text: string): number | null => {
  const m = /^\s*(\d+)(?:\.(\d{1,2}))?\s*$/.exec(text)
  return m ? Number(m[1]) * 100 + Number((m[2] ?? '').padEnd(2, '0') || 0) : null
}
