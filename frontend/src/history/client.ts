import { arr, defaultFetch, jsonInit, num, pick, readWire, rec, str } from '../lib/wire'
import type { FetchImpl } from '../lib/wire'
import type { Divergence, HistoryClient, HistoryItem, InForce, PaymentHistoryData, PolicyAsOf, PolicyRef } from './types'

const policyRef = (raw: unknown): PolicyRef => {
  const o = rec(raw)
  return { policyId: str(pick(o, 'policy_id', 'policyId', 'id')) ?? '?', version: num(o.version) ?? null, hash: str(o.hash) ?? '', mandatory: o.mandatory === true }
}

export function normalizeDivergence(raw: unknown): Divergence | null {
  if (!raw || typeof raw !== 'object') return null
  const o = rec(raw)
  return {
    decisionSeq: num(pick(o, 'decision_seq', 'decisionSeq')) ?? null,
    recordedVerdict: str(pick(o, 'recorded_verdict', 'recordedVerdict')) ?? null,
    allowedThen: pick(o, 'allowed_then', 'allowedThen') === true,
    wouldNow: str(pick(o, 'would_now', 'wouldNow')) ?? null,
    differing: arr(pick(o, 'differing_clauses', 'differingClauses')).map((d) => {
      const x = rec(d)
      return { policyId: str(pick(x, 'policy_id', 'policyId')) ?? null, clause: str(x.clause) ?? '?', then: str(x.then) ?? null, now: str(x.now) ?? null, why: str(x.why) ?? null }
    }),
    summary: str(o.summary) ?? '',
  }
}

const inForce = (raw: unknown): InForce => {
  const o = rec(raw)
  return { effectiveHash: str(pick(o, 'effective_policy_hash', 'effectiveHash')) ?? null, policies: arr(o.policies).map(policyRef), summary: str(o.summary) ?? null }
}

export function normalizeItem(raw: unknown): HistoryItem | null {
  const o = rec(raw)
  const seq = num(o.seq)
  if (seq === undefined) return null
  const recordedAt = str(pick(o, 'recorded_at', 'recordedAt')) ?? ''
  return {
    seq, kind: str(o.kind) ?? 'unknown', category: str(o.category) ?? 'other', actor: str(o.actor) ?? 'system', summary: str(o.summary) ?? '',
    recordedAt, validAt: str(pick(o, 'valid_at', 'validAt')) ?? recordedAt,
    validTimeSource: pick(o, 'valid_time_source', 'validTimeSource') === 'explicit' ? 'explicit' : 'recorded_time',
    lateArrival: pick(o, 'late_arrival', 'lateArrival') === true,
    supersedes: num(o.supersedes) ?? null,
    inForce: inForce(pick(o, 'policy_in_force', 'policyInForce')),
    verdict: str(o.verdict) ?? null,
    reproduced: typeof o.reproduced === 'boolean' ? o.reproduced : typeof rec(o.reproduced).reproduced === 'boolean' ? (rec(o.reproduced).reproduced as boolean) : null,
    clauses: arr(o.clauses).map((c) => ({ clause: str(rec(c).clause) ?? '?', outcome: str(rec(c).outcome) ?? null })),
    divergence: normalizeDivergence(o.divergence),
  }
}

export function normalizePayment(raw: unknown, id: string): PaymentHistoryData {
  const o = rec(raw)
  return {
    missionId: str(pick(o, 'mission_id', 'missionId')) ?? id,
    requestedId: str(pick(o, 'requested_id', 'requestedId')) ?? id,
    summary: str(o.summary) ?? '',
    items: arr(o.timeline).map(normalizeItem).filter((x): x is HistoryItem => x !== null),
    divergence: normalizeDivergence(o.divergence),
  }
}

export function normalizePolicyAsOf(raw: unknown): PolicyAsOf {
  const o = rec(raw)
  return {
    at: str(o.at) ?? '', basis: str(o.basis) ?? 'valid',
    effectiveHash: str(pick(o, 'effective_policy_hash', 'effectiveHash')) ?? null,
    summary: str(o.summary) ?? null,
    policies: arr(o.attachments).map(policyRef),
  }
}

export function createHistoryHttpClient(base = '/api/history', fetchImpl: FetchImpl = defaultFetch): HistoryClient {
  // Demo auth only, same as audit/client.ts.
  const demoAuth = { 'X-Party-Role': 'admin', 'X-Party-Id': 'console' }
  const get = (path: string) => {
    const init = jsonInit('GET')
    return fetchImpl(base + path, { ...init, headers: { ...(init.headers as Record<string, string>), ...demoAuth } }).then((r) => readWire<unknown>(r))
  }
  return {
    kind: 'http',
    payment: (id) => get(`/payment/${encodeURIComponent(id)}`).then((r) => normalizePayment(r, id)),
    policyAsOf: (q) =>
      get(`/policy-as-of?${new URLSearchParams({ target_type: q.targetType, target_id: q.targetId, at: q.at, basis: q.basis ?? 'valid' })}`).then(normalizePolicyAsOf),
  }
}

const ref = (v: number, hash: string): PolicyRef => ({ policyId: 'org-base', version: v, hash, mandatory: true })
const fxItem = (seq: number, e: Partial<HistoryItem>): HistoryItem => ({
  seq, kind: 'input.event', category: 'input', actor: 'system', summary: '', recordedAt: `2026-10-15T10:0${seq}:00Z`, validAt: `2026-10-15T10:0${seq}:00Z`,
  validTimeSource: 'explicit', lateArrival: false, supersedes: null, inForce: { effectiveHash: 'h-v1', policies: [ref(1, 'h-v1')], summary: null },
  verdict: null, reproduced: null, clauses: [], divergence: null, ...e,
})
const FIXTURE_DIV: Divergence = {
  decisionSeq: 3, recordedVerdict: 'allow', allowedThen: true, wouldNow: 'hold',
  differing: [{ policyId: 'org-base', clause: 'APPROVE-1', then: 'allow', now: 'hold', why: 'limit lowered from 5000 to 3000 minor units (org-base v1 -> v2)' }],
  summary: 'Allowed under the policy at the time; would be hold under current policy because APPROVE-1: limit lowered from 5000 to 3000 minor units.',
}

/** Explicit fixture mode: canned history, flagged by kind 'fixture'. */
export function createHistoryFixtureClient(): HistoryClient {
  const items = [
    fxItem(1, { summary: 'Mission created, 42.00 USD' }),
    fxItem(3, { kind: 'decision', category: 'decision', summary: 'decision allow', verdict: 'allow', reproduced: true, clauses: [{ clause: 'PAY-1', outcome: 'allow' }, { clause: 'APPROVE-1', outcome: 'allow' }], divergence: FIXTURE_DIV, validAt: '2026-10-15T10:30:00Z', recordedAt: '2026-10-15T10:31:00Z' }),
    fxItem(2, { summary: 'Position fix arrived late', lateArrival: true, validAt: '2026-10-15T10:10:00Z', recordedAt: '2026-10-15T10:32:00Z' }),
  ]
  return {
    kind: 'fixture',
    payment: async (id) => ({ missionId: id, requestedId: id, summary: `${items.length} events (FIXTURE)`, items, divergence: FIXTURE_DIV }),
    policyAsOf: async (q) => ({ at: q.at, basis: q.basis ?? 'valid', effectiveHash: 'h-v1', summary: 'FIXTURE', policies: [ref(1, 'h-v1')] }),
  }
}
