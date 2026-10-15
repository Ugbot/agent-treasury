import { arr, defaultFetch, jsonInit, num, pick, readWire, rec, str } from '../lib/wire'
import type { FetchImpl } from '../lib/wire'
import type { AgentStep, RunMode, RunResult, ScenarioInfo, ScenariosClient } from './types'

const FOUR: ScenarioInfo[] = [
  { id: 'delivery-pay', title: '1. Delivery confirmed: pay merchant and driver' },
  { id: 'fuel-card', title: '2. Driver fuel: capped card authorisation' },
  { id: 'coverage-replan', title: '3. Coverage changes: agent replans, policy checks' },
  { id: 'cancel-refund', title: '4. Cancel before pickup: refund' },
]

export function normalizeRun(raw: unknown, id: string): RunResult {
  const o = rec(raw)
  const un = pick(o, 'unavailable', 'unavailable_reason')
  return {
    scenarioId: str(pick(o, 'scenario', 'scenarioId', 'scenario_id')) ?? id,
    missionId: str(pick(o, 'missionId', 'mission_id')) ?? null,
    status: str(o.status) ?? 'unknown',
    mode: str(o.mode) ?? null,
    steps: arr(o.steps).map((s) => {
      const x = rec(s)
      return { name: str(pick(x, 'name', 'step', 'label')) ?? '?', status: str(x.status) ?? '', detail: str(pick(x, 'detail', 'message', 'summary')) ?? null }
    }),
    unavailable: typeof un === 'string' ? un : un === true ? str(o.message) ?? 'unavailable' : null,
  }
}

export function normalizeStep(raw: unknown): AgentStep {
  const o = rec(raw)
  const p = pick(o, 'proposal')
  const v = pick(o, 'verdict', 'policy_verdict', 'policyVerdict')
  const vo = typeof v === 'object' && v ? rec(v) : { verdict: v }
  const un = pick(o, 'unavailable', 'unavailable_reason')
  const available = o.available !== false && !un
  const po = rec(p)
  return {
    available,
    unavailable: available ? null : typeof un === 'string' ? un : str(o.message) ?? 'Agent unavailable',
    mode: str(o.mode) ?? null,
    proposal: p
      ? {
          action: str(pick(po, 'action', 'kind', 'type')) ?? '?',
          summary: str(pick(po, 'summary', 'description')) ?? null,
          amountMinor: num(pick(po, 'amountMinor', 'amount_minor')) ?? null,
          currency: str(po.currency) ?? null,
          recipient: str(pick(po, 'recipient', 'recipientId', 'recipient_id', 'counterparty')) ?? null,
          rationale: str(pick(po, 'rationale', 'reason', 'why')) ?? null,
          agent: str(pick(po, 'agent', 'source')) ?? str(o.agent) ?? 'agent',
        }
      : null,
    verdict: v
      ? {
          verdict: str(vo.verdict) ?? null,
          executed: typeof pick(vo, 'executed') === 'boolean' ? (pick(vo, 'executed') as boolean) : typeof o.executed === 'boolean' ? o.executed : null,
          reasons: arr(vo.reasons).filter((x): x is string => typeof x === 'string'),
          clauses: arr(pick(vo, 'clauses', 'results')).map((c) => {
            const x = rec(c)
            const ev = x.evidence
            return {
              clauseId: str(pick(x, 'clauseId', 'clause_id', 'clause', 'id')) ?? '?',
              outcome: str(x.outcome) ?? (typeof x.passed === 'boolean' ? (x.passed ? 'allow' : 'hold') : null),
              detail: ev === undefined ? null : typeof ev === 'string' ? ev : JSON.stringify(ev),
            }
          }),
        }
      : null,
    auditSeq: num(pick(o, 'auditSeq', 'audit_seq')) ?? null,
  }
}

// Console scenario-rail ids; they are not runnable through /scenarios/{id}/run.
const LEGACY_CONSOLE_SCENARIOS = new Set(['happy-path', 'prohibited-zone', 'over-limit'])

export function createScenariosHttpClient(base = '', fetchImpl: FetchImpl = defaultFetch): ScenariosClient {
  const call = (method: string, path: string, body?: unknown) => fetchImpl(base + path, jsonInit(method, body)).then((r) => readWire<unknown>(r))
  return {
    kind: 'http',
    list: () =>
      call('GET', '/api/scenarios').then((raw) =>
        arr(raw).map((s) => ({ id: str(rec(s).id) ?? '', title: str(rec(s).title) ?? str(rec(s).id) ?? '' })).filter((s) => s.id && !LEGACY_CONSOLE_SCENARIOS.has(s.id))),
    run: (id, mode) => call('POST', `/api/scenarios/${encodeURIComponent(id)}/run`, { mode }).then((raw) => normalizeRun(raw, id)),
    step: (missionId, agent = 'auto') => call('POST', '/api/agent/step', { missionId, agent }).then(normalizeStep),
  }
}

/** Explicit fixture mode: nothing runs and nothing is paid. Results say so. */
export function createScenariosFixtureClient(): ScenariosClient {
  return {
    kind: 'fixture',
    list: async () => FOUR,
    run: async (id, mode: RunMode) => ({
      scenarioId: id, missionId: 'fixture-mission', status: 'simulated', mode: 'fixture',
      steps: [{ name: 'fixture', status: 'simulated', detail: `Fixture mode: no backend call was made (requested ${mode}).` }],
      unavailable: null,
    }),
    step: async () => normalizeStep({
      agent: 'goap', mode: 'fixture',
      proposal: { action: 'pay_merchant', summary: 'Pay merchant for confirmed delivery', amountMinor: 4200, currency: 'usd', recipient: 'merchant', rationale: 'Receipt confirmed, route compliant.' },
      verdict: { verdict: 'allow', executed: false, clauses: [{ clause: 'PAY-1', outcome: 'allow' }, { clause: 'APPROVE-1', outcome: 'allow' }] },
    }),
  }
}

export function scenariosClientFromEnv(mode: string | undefined = import.meta.env.VITE_API_MODE): ScenariosClient {
  return mode === 'fixture' ? createScenariosFixtureClient() : createScenariosHttpClient()
}
