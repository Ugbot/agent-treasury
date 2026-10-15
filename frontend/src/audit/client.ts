import { arr, defaultFetch, jsonInit, num, pick, readWire, rec, str } from '../lib/wire'
import type { FetchImpl } from '../lib/wire'
import type { AuditClient, AuditEntry, AuditPolicy, AuditQuery, AuditStripe, ChainStatus } from './types'

/** Defensive: a row from the server is data, not trusted shape. */
export function normalizeEntry(raw: unknown): AuditEntry | null {
  const o = rec(raw)
  const seq = num(o.seq)
  if (seq === undefined) return null
  const obj = (v: unknown) => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null)
  return {
    seq,
    ts: str(o.ts) ?? '',
    actor: str(o.actor) ?? 'system',
    party: str(o.party) ?? null,
    mission_id: str(pick(o, 'mission_id', 'missionId')) ?? null,
    kind: str(o.kind) ?? 'unknown',
    summary: str(o.summary) ?? '',
    payload: obj(o.payload) ?? {},
    policy: obj(o.policy) as AuditPolicy | null,
    stripe: obj(o.stripe) as AuditStripe | null,
    mode: str(o.mode) ?? null,
    prev_hash: str(pick(o, 'prev_hash', 'prevHash')) ?? '',
    hash: str(o.hash) ?? '',
  }
}

export function normalizeList(raw: unknown): AuditEntry[] {
  const list = Array.isArray(raw) ? raw : arr(rec(raw).entries ?? rec(raw).items)
  return list.map(normalizeEntry).filter((e): e is AuditEntry => e !== null)
}

export function normalizeChain(raw: unknown): ChainStatus {
  const o = rec(raw)
  return {
    ok: o.ok === true,
    entries: num(o.entries) ?? 0,
    head: str(o.head),
    broken_at_seq: num(pick(o, 'broken_at_seq', 'brokenAtSeq')),
  }
}

const qs = (q: Record<string, string | number | undefined>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== '') p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}

export function createAuditHttpClient(base = '/api/audit', fetchImpl: FetchImpl = defaultFetch): AuditClient {
  // Demo auth only (see routes_audit.py): the console reads as admin; per-party scoping is enforced server-side
  // and covered by backend tests. EventSource cannot send headers, so streams fall back to polling.
  const demoAuth = { 'X-Party-Role': 'admin', 'X-Party-Id': 'console' }
  const get = (path: string) => {
    const init = jsonInit('GET')
    return fetchImpl(base + path, { ...init, headers: { ...(init.headers as Record<string, string>), ...demoAuth } }).then((r) => readWire<unknown>(r))
  }
  return {
    kind: 'http',
    list: (q: AuditQuery = {}) =>
      get(qs({ mission: q.mission, party: q.party, kind: q.kind, actor: q.actor, after: q.after, limit: q.limit })).then(normalizeList),
    party: (party, after = 0) => get(`/party/${encodeURIComponent(party)}${qs({ after })}`).then(normalizeList),
    verify: () => get('/verify').then(normalizeChain),
    // EventSource cannot send the demo-auth headers (/stream returned 401), so the feed polls every second instead.
    openStream: () => null,
  }
}

const T0 = Date.parse('2026-10-15T18:30:00Z')
const fx = (seq: number, e: Partial<AuditEntry>): AuditEntry => ({
  seq, ts: new Date(T0 + seq * 20000).toISOString(), actor: 'system', party: null, mission_id: 'fixture-mission',
  kind: 'input.event', summary: '', payload: {}, policy: null, stripe: null, mode: 'fixture',
  prev_hash: `fixture-${seq - 1}`, hash: `fixture-${seq}`, ...e,
})

/** Explicit fixture mode: canned entries, labelled FIXTURE. The chain badge reports fixture data, not a real verification. */
export function createAuditFixtureClient(): AuditClient {
  const rows: AuditEntry[] = [
    fx(1, { actor: 'sender', party: 'sender', kind: 'input.event', summary: 'Sender created order for 42.00 USD' }),
    fx(2, { actor: 'system', party: 'sender', kind: 'policy.attach', summary: 'org-base v1 attached (mandatory)', policy: { id: 'org-base', version: 1, hash: 'fixture0001' } }),
    fx(3, { actor: 'driver', party: 'driver', kind: 'input.event', summary: 'Driver reported position at depot' }),
    fx(4, { actor: 'agent', party: null, kind: 'agent.proposal', summary: 'Agent proposes: pay merchant 42.00 USD', payload: { action: 'pay_merchant' } }),
    fx(5, { actor: 'system', party: 'sender', kind: 'decision', summary: 'Hold: PAY-1 requires receipt', policy: { id: 'org-base', version: 1, hash: 'fixture0001', verdict: 'hold', clauses: [{ clause: 'PAY-1', outcome: 'hold' }] } }),
    fx(6, { actor: 'receiver', party: 'receiver', kind: 'delivery.proof', summary: 'Receiver confirmed receipt' }),
  ]
  const byParty = (p: string, after: number) => rows.filter((r) => r.seq > after && (r.party === p || r.party === null))
  return {
    kind: 'fixture',
    list: async (q = {}) => rows.filter((r) => r.seq > (q.after ?? 0) && (!q.party || r.party === q.party) && (!q.kind || r.kind === q.kind)),
    party: async (p, after = 0) => byParty(p, after).filter((r) => r.party === p),
    verify: async () => ({ ok: true, entries: rows.length, head: rows[rows.length - 1].hash }),
    openStream: () => null,
  }
}

export function auditClientFromEnv(mode: string | undefined = import.meta.env.VITE_API_MODE): AuditClient {
  return mode === 'fixture' ? createAuditFixtureClient() : createAuditHttpClient()
}
