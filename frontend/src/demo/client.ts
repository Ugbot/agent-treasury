import { defaultFetch, jsonInit, readWire, rec, str } from '../lib/wire'
import type { FetchImpl } from '../lib/wire'

export interface DemoRunOutcome {
  ok: boolean
  mode: string | null
  paymentId: string | null
  note: string
}

export interface DemoClient {
  kind: 'http' | 'fixture'
  /** Runs a scenario under a fresh run id. Prefers stripe_test, falls back to mock, always reports the real mode. */
  run(scenario: string, runId: string): Promise<DemoRunOutcome>
  freezeAvailable(): Promise<boolean>
}

function outcome(raw: unknown): DemoRunOutcome {
  const o = rec(raw)
  const mode = str(o.mode) ?? null
  const unavailable = o.status === 'unavailable'
  if (unavailable) {
    const why = rec(o.unavailable)
    return { ok: false, mode, paymentId: null, note: str(why.message) ?? str(why.reason) ?? 'a capability is unavailable (see Policies and the log)' }
  }
  return { ok: true, mode, paymentId: str(o.mission_id) ?? str(o.missionId) ?? str(o.run_id) ?? null, note: mode === 'stripe_test' ? 'STRIPE TEST' : 'MOCK' }
}

export function createDemoHttpClient(fetchImpl: FetchImpl = defaultFetch, base = ''): DemoClient {
  const post = (id: string, body: unknown) => {
    const init = jsonInit('POST', body)
    // The scenario pauses at human-approval holds; the demo plays the approver (demo auth, recorded in the audit log).
    const headers = { ...(init.headers as Record<string, string>), 'X-Actor-Role': 'approver', 'X-Actor-Id': 'demo-approver' }
    return fetchImpl(`${base}/api/scenarios/${encodeURIComponent(id)}/run`, { ...init, headers }).then((r) => readWire<unknown>(r))
  }
  /** Run, then approve every pending leg (up to 3 rounds) so money really moves under policy. */
  const runApproved = async (id: string, body: Record<string, unknown>) => {
    let res = await post(id, body)
    for (let i = 0; i < 3; i++) {
      const o = rec(res)
      const needed = Array.isArray(o.approvals_needed) ? o.approvals_needed : []
      if (o.status !== 'awaiting_approval' || needed.length === 0) break
      const kinds = needed.map((n) => str(rec(n).kind)).filter((k): k is string => !!k)
      res = await post(id, { ...body, approvals: kinds })
    }
    return res
  }
  return {
    kind: 'http',
    async run(scenario, runId) {
      try {
        return outcome(await runApproved(scenario, { run_id: runId, mode: 'stripe_test' }))
      } catch {
        try {
          return outcome(await runApproved(scenario, { run_id: runId, mode: 'mock' }))
        } catch (e) {
          return { ok: false, mode: null, paymentId: null, note: e instanceof Error ? e.message : 'run failed' }
        }
      }
    },
    freezeAvailable: () => fetchImpl(`${base}/api/freeze`, { headers: { accept: 'application/json' } }).then((r) => r.ok, () => false),
  }
}

export function createDemoFixtureClient(): DemoClient {
  return {
    kind: 'fixture',
    run: async () => ({ ok: true, mode: 'fixture', paymentId: null, note: 'FIXTURE (nothing runs)' }),
    freezeAvailable: async () => false,
  }
}
