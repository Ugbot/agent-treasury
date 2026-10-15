import type { FreezeClient, FreezeState } from './types'

export const NOT_FROZEN: FreezeState = { active: false, version: 1, since: null, by: null, reason: null }

export function createFreezeFixtureClient(initial: FreezeState = NOT_FROZEN): FreezeClient {
  let state = initial
  return {
    kind: 'fixture',
    get: async () => state,
    set: async (active, reason) => {
      state = { active, version: state.version + 1, since: new Date().toISOString(), by: 'approver', reason }
      return state
    },
  }
}

async function unwrap(res: Response): Promise<FreezeState> {
  let body: { data?: FreezeState; error?: { message?: string } } | null = null
  try { body = await res.json() } catch { body = null }
  if (!res.ok || !body?.data) {
    const e = new Error(body?.error?.message ?? `Unexpected response (${res.status})`) as Error & { status: number }
    e.status = res.status
    throw e
  }
  return body.data
}

/** HTTP client; falls back to a fixture when the endpoint is not there yet (404). */
export function createFreezeHttpClient(baseUrl = '', fetchImpl: typeof fetch = (...a) => fetch(...a)): FreezeClient {
  const fallback = createFreezeFixtureClient()
  const url = baseUrl + '/api/freeze'
  const is404 = (e: unknown) => (e as { status?: number })?.status === 404
  return {
    kind: 'http',
    get: () => fetchImpl(url, { headers: { accept: 'application/json' } }).then(unwrap).catch((e) => { if (is404(e)) return fallback.get(); throw e }),
    set: (active, reason) =>
      fetchImpl(url, { method: 'POST', headers: { 'content-type': 'application/json', 'X-Actor-Role': 'approver' }, body: JSON.stringify({ active, reason }) })
        .then(unwrap)
        .catch((e) => { if (is404(e)) return fallback.set(active, reason); throw e }),
  }
}

export const freezeClientFromEnv = (fixture: boolean): FreezeClient => (fixture ? createFreezeFixtureClient() : createFreezeHttpClient())
