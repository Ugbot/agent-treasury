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
  return { ok: true, mode, paymentId: str(o.mission_id) ?? str(o.missionId) ?? str(o.run_id) ?? null, note: mode === 'stripe_test' ? 'STRIPE TEST' : 'MOCK' }
}

export function createDemoHttpClient(fetchImpl: FetchImpl = defaultFetch, base = ''): DemoClient {
  const post = (id: string, body: unknown) => fetchImpl(`${base}/api/scenarios/${encodeURIComponent(id)}/run`, jsonInit('POST', body)).then((r) => readWire<unknown>(r))
  return {
    kind: 'http',
    async run(scenario, runId) {
      try {
        return outcome(await post(scenario, { run_id: runId, mode: 'stripe_test' }))
      } catch {
        try {
          return outcome(await post(scenario, { run_id: runId, mode: 'mock' }))
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
