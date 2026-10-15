export interface MemoryStatus {
  ready: boolean
  backend?: string
  message?: string
  mode?: string
  configured?: boolean
  reachable?: boolean
  last_write?: { at?: string; status?: string; message?: string; ref?: string } | null
}
export interface MemoryAnswer {
  source: string
  question: string
  clause_ids: string[]
  policy_id?: string | null
  policy_version?: number | string | null
  evidence: string[]
  note?: string
}
export interface SyncResult { written?: number; count?: number; [k: string]: unknown }
export interface MemoryClient {
  kind: 'http' | 'fixture'
  status(): Promise<MemoryStatus>
  explain(question: string): Promise<MemoryAnswer>
  sync(): Promise<SyncResult>
}

const HEADERS = { 'content-type': 'application/json', accept: 'application/json', 'X-Party-Role': 'admin', 'X-Party-Id': 'console' }

export function createMemoryHttpClient(base = '/api/policies/memory', f: typeof fetch = (...a) => fetch(...a)): MemoryClient {
  const call = async <T,>(method: string, path: string, body?: unknown): Promise<T> => {
    const r = await f(base + path, { method, headers: HEADERS, body: body === undefined ? undefined : JSON.stringify(body) })
    if (!r.ok) {
      let d = ''
      try { d = ((await r.json()) as { detail?: string }).detail ?? '' } catch { /* ignore */ }
      throw Object.assign(new Error(d || `Request failed (${r.status})`), { status: r.status })
    }
    return (await r.json()) as T
  }
  return {
    kind: 'http',
    status: () => call<MemoryStatus>('GET', '/status'),
    explain: (question) => call<MemoryAnswer>('POST', '/explain', { mission_id: 'org', question }),
    sync: () => call<SyncResult>('POST', '/sync', {}),
  }
}

export function createMemoryFixtureClient(): MemoryClient {
  return {
    kind: 'fixture',
    status: async () => ({ ready: false, backend: 'fixture', message: 'Fixture mode: no backend', configured: false, reachable: false }),
    explain: async (question) => ({
      source: 'audit_log', question, clause_ids: ['TRAVEL-1'], policy_id: 'org-travel', policy_version: 1,
      evidence: ['Fixture answer: TRAVEL-1 blocks payment when the route enters a prohibited zone.'], note: 'Fixture data',
    }),
    sync: async () => ({ written: 0 }),
  }
}

export async function memoryClientFromEnv(): Promise<MemoryClient> {
  const http = createMemoryHttpClient()
  try {
    await http.status()
    return http
  } catch (e) {
    if ((e as { status?: number }).status === 404) return createMemoryFixtureClient()
    return http
  }
}
