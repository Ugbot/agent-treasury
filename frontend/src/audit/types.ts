/** Wire types for src/treasury/audit.py rows (snake_case). */
export interface AuditPolicy {
  id?: string
  version?: number
  hash?: string
  verdict?: string
  clauses?: unknown[]
  [k: string]: unknown
}
export interface AuditStripe {
  ids?: string[]
  livemode?: boolean
  op?: string
  [k: string]: unknown
}
export interface AuditEntry {
  seq: number
  ts: string
  actor: string
  party: string | null
  mission_id: string | null
  kind: string
  summary: string
  payload: Record<string, unknown>
  policy: AuditPolicy | null
  stripe: AuditStripe | null
  mode: string | null
  prev_hash: string
  hash: string
}

export interface ChainStatus {
  ok: boolean
  entries: number
  head?: string
  broken_at_seq?: number
}

export interface AuditQuery {
  mission?: string
  party?: string
  kind?: string
  actor?: string
  after?: number
  limit?: number
}

export type Transport = 'sse' | 'polling' | 'idle'

export interface AuditClient {
  kind: 'http' | 'fixture'
  list(q?: AuditQuery): Promise<AuditEntry[]>
  /** Role-scoped: the server returns only what this party may see. */
  party(party: string, after?: number): Promise<AuditEntry[]>
  verify(): Promise<ChainStatus>
  /** Open the SSE stream; null when unsupported (the hook then polls with after=seq). */
  openStream(
    q: { party?: string; mission?: string; after: number },
    onEntries: (e: AuditEntry[]) => void,
    onError: () => void,
  ): (() => void) | null
}

export const verdictOf = (e: AuditEntry): string | undefined => {
  const v = e.policy?.verdict ?? e.payload?.verdict
  return typeof v === 'string' ? v : undefined
}
