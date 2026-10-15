import type {
  Effective, JobDraft, Job, JourneyClient, LibraryPolicy, PreviewResult, Watch, WatchStep,
} from './types'
import { JourneyApiError } from './types'

type Fetch = typeof fetch

async function parse<T>(res: Response): Promise<T> {
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  if (!res.ok) {
    const d = (body as { detail?: unknown } | null)?.detail
    throw new JourneyApiError(res.status, typeof d === 'string' ? d : `Request failed (${res.status})`)
  }
  return body as T
}

/** Talks to routes_jobs.py. `base` is wherever the integrator mounts it (default /api). */
export function createJourneyHttpClient(base = '/api', fetchImpl: Fetch = (...a) => fetch(...a)): JourneyClient {
  const call = (method: string, path: string, body?: unknown) =>
    fetchImpl(base + path, {
      method,
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  const id = encodeURIComponent
  return {
    kind: 'http',
    library: () => call('GET', '/jobs/policy-library').then((r) => parse<LibraryPolicy[]>(r)),
    createJob: (d) => call('POST', '/jobs', d).then((r) => parse<Job>(r)),
    effective: (j) => call('GET', `/jobs/${id(j)}/policies`).then((r) => parse<Effective>(r)),
    attach: (j, policyId, expectedVersion, params) =>
      call('POST', `/jobs/${id(j)}/policies`, { policy_id: policyId, expected_version: expectedVersion, params })
        .then((r) => parse<Effective>(r)),
    preview: (j) => call('POST', `/jobs/${id(j)}/preview`, {}).then((r) => parse<{ results: PreviewResult[] }>(r)).then((b) => b.results),
    dispatch: (j) => call('POST', `/jobs/${id(j)}/dispatch`).then((r) => parse<Job>(r)),
    watch: (j) => call('GET', `/jobs/${id(j)}/watch`).then((r) => parse<Watch>(r)),
  }
}

// ---------------------------------------------------------------- fixture (no backend)

const ORG = {
  attachment_id: 'fx-org', policy_id: 'org-base', version: 1, title: 'Organisation base policy', mandatory: true,
  locked: true, scope: 'organisation' as const,
  clauses: [
    { id: 'CURRENCY-1', prose: "Only settle in the organisation's permitted currencies", kind: 'allowed_currency', params: { currencies: ['usd'] } },
    { id: 'PAY-1', prose: 'Release only after delivery proof', kind: 'require_input', params: { key: 'receipt_confirmed' } },
    { id: 'TRAVEL-1', prose: 'Never pay when route enters a prohibited zone', kind: 'forbid_input', params: { key: 'prohibited_zone' } },
    { id: 'APPROVE-1', prose: 'Amounts above USD 50.00 need approval', kind: 'max_amount', params: { limit_minor: 5000 } },
  ],
}

const LIB: LibraryPolicy[] = [
  { policy_id: 'org-base', version: 1, title: ORG.title, summary: 'Delivery proof, safe route, approval above $50.00 and USD only.', template: false, mandatory: true, locked: true, parameter: null },
  { policy_id: 'tpl-fuel-meal', version: 1, title: 'Fuel and meal allowance', summary: 'Allowance payments above $30.00 need approval before they are released.', template: true, mandatory: false, locked: false, parameter: null },
  { policy_id: 'tpl-insurance', version: 1, title: 'Insurance required', summary: 'Payment waits until valid insurance cover is confirmed for the goods.', template: true, mandatory: false, locked: false, parameter: null },
  { policy_id: 'tpl-approve-above', version: 1, title: 'Approve above a limit', summary: 'Any payment above the limit you choose needs a human approval.', template: true, mandatory: false, locked: false, parameter: { name: 'limit_minor', label: 'Approval limit (minor units)', default: 2500 } },
  { policy_id: 'tpl-refund-before-pickup', version: 1, title: 'Refund before pickup', summary: 'Refunds are only allowed before pickup; a refund request after pickup is stopped.', template: true, mandatory: false, locked: false, parameter: null },
]

const CLAUSE: Record<string, { id: string; prose: string; kind: string; params: Record<string, unknown> }> = {
  'tpl-fuel-meal': { id: 'ALLOW-1', prose: 'Fuel and meal allowance above USD 30.00 needs approval', kind: 'max_amount', params: { limit_minor: 3000 } },
  'tpl-insurance': { id: 'INSURE-1', prose: 'Release only while insurance cover is valid', kind: 'require_input', params: { key: 'insurance_valid' } },
  'tpl-approve-above': { id: 'LIMIT-1', prose: 'Payments above the chosen limit need approval', kind: 'max_amount', params: { limit_minor: 2500 } },
  'tpl-refund-before-pickup': { id: 'REFUNDWIN-1', prose: 'Refund requested after pickup is not allowed', kind: 'forbid_input', params: { key: 'refund_requested_after_pickup' } },
}

/** In-memory stand-in. Every watch() call advances the story one step so polling is visible. */
export function createJourneyFixtureClient(): JourneyClient {
  const jobs = new Map<string, Job>()
  const attached = new Map<string, EffectiveRuleLike[]>()
  const polls = new Map<string, number>()
  let n = 0
  type EffectiveRuleLike = Effective['rules'][number]
  const eff = (j: string): Effective => ({
    job_id: j, target_version: attached.get(j)?.length ?? 0, frozen: jobs.get(j)?.status === 'dispatched',
    rules: [ORG, ...(attached.get(j) ?? [])],
  })
  const need = (j: string) => {
    const job = jobs.get(j)
    if (!job) throw new JourneyApiError(404, `unknown job ${j}`)
    return job
  }
  return {
    kind: 'fixture',
    library: async () => LIB,
    createJob: async (d: JobDraft) => {
      const job: Job = {
        ...d, job_id: `job_fixture${++n}`, mission_id: `mission-job_fixture${n}`, status: 'draft', currency: 'usd',
        total_minor: d.price_minor + d.delivery_fee_minor, mode: 'mock',
      }
      jobs.set(job.job_id, job)
      attached.set(job.job_id, [])
      if (d.insurance_required) {
        attached.set(job.job_id, [{ attachment_id: 'fx-ins', policy_id: 'tpl-insurance', version: 1, title: 'Insurance required', mandatory: false, locked: true, scope: 'job', clauses: [CLAUSE['tpl-insurance']] }])
      }
      return job
    },
    effective: async (j) => (need(j), eff(j)),
    attach: async (j, policyId, expected, params) => {
      need(j)
      const cur = attached.get(j) ?? []
      if (expected !== cur.length) throw new JourneyApiError(409, 'stale target version')
      if (cur.some((r) => r.policy_id === policyId)) throw new JourneyApiError(409, 'already attached')
      const lib = LIB.find((p) => p.policy_id === policyId)
      const cl = CLAUSE[policyId]
      if (!lib || !cl) throw new JourneyApiError(400, `${policyId} cannot be attached`)
      const clause = params?.limit_minor ? { ...cl, params: { limit_minor: Number(params.limit_minor) } } : cl
      attached.set(j, [...cur, { attachment_id: `fx-${policyId}`, policy_id: policyId, version: 1, title: lib.title, mandatory: false, locked: false, scope: 'job', clauses: [clause] }])
      return eff(j)
    },
    preview: async (j) => {
      const job = need(j)
      const ids = (attached.get(j) ?? []).map((r) => r.policy_id)
      return [
        { situation: 'happy_path', label: 'Everything goes to plan', verdict: 'allow', clause: null, policy_id: null, reason: 'All effective rules are satisfied; payment would be released.', amount_minor: job.total_minor, simulated: true },
        { situation: 'receipt_missing', label: 'Delivery receipt missing', verdict: 'hold', clause: 'PAY-1', policy_id: 'org-base', reason: 'Held: delivery receipt has not been confirmed. Release only after delivery proof.', amount_minor: job.total_minor, simulated: true },
        { situation: 'prohibited_zone', label: 'Courier enters a prohibited zone', verdict: 'deny', clause: 'TRAVEL-1', policy_id: 'org-base', reason: 'Stopped: the route entered a prohibited zone. Never pay when route enters a prohibited zone.', amount_minor: job.total_minor, simulated: true },
        { situation: 'over_limit', label: 'Amount goes over an approval limit', verdict: 'hold', clause: 'APPROVE-1', policy_id: 'org-base', reason: 'Held for approval: $50.01 is above the $50.00 limit.', amount_minor: 5001, simulated: true },
        { situation: 'insurance_lapsed', label: 'Insurance cover has lapsed', ...(ids.includes('tpl-insurance')
          ? { verdict: 'hold' as const, clause: 'INSURE-1', policy_id: 'tpl-insurance', reason: 'Held: insurance cover has not been confirmed as valid. Release only while insurance cover is valid.' }
          : { verdict: 'allow' as const, clause: null, policy_id: null, reason: 'No attached rule cares about insurance, so payment would be released.' }),
        amount_minor: job.total_minor, simulated: true },
      ]
    },
    dispatch: async (j) => {
      const job = need(j)
      const already = job.status === 'dispatched'
      job.status = 'dispatched'
      return { ...job, already_dispatched: already }
    },
    watch: async (j) => {
      need(j)
      const k = (polls.get(j) ?? 0) + 1
      polls.set(j, k)
      const script: [string, string, string][] = [
        ['job.dispatched', 'sender', 'Job dispatched with its policies frozen.'],
        ['job.workflow_started', 'system', 'Payment workflow started; it now waits for evidence.'],
        ['decision', 'system', 'Policy check: payment is on hold. Held: delivery receipt has not been confirmed. Release only after delivery proof.'],
        ['input.event', 'receiver', 'New information received: delivery receipt.'],
        ['decision', 'system', 'Policy check passed: payment may be released.'],
        ['payment', 'system', 'Payment confirmed (simulated, no real money).'],
      ]
      const steps: WatchStep[] = script.slice(0, k).map(([kind, actor, text], i) => ({
        seq: i + 1, ts: new Date(0).toISOString(), kind, actor, party: actor === 'sender' ? 'sender' : null, text, mode: 'mock', simulated: true,
      }))
      const done = k >= script.length
      return { job_id: j, status: done ? 'paid' : k >= 3 ? 'held' : 'dispatched', mode: 'mock', steps, last_seq: steps.length, done }
    },
  }
}

/** Fixture unless VITE_JOURNEY_API=live (or a base path) is set. */
export function journeyClientFromEnv(): JourneyClient {
  const v = (import.meta as unknown as { env?: Record<string, string | undefined> }).env?.VITE_JOURNEY_API
  return v ? createJourneyHttpClient(v === 'live' ? '/api' : v) : createJourneyFixtureClient()
}
