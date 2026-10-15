import { useEffect, useRef, useState } from 'react'
import type { AuditClient, AuditEntry, Transport } from './types'

export interface FeedOptions {
  party?: string
  mission?: string
  /** Polling cadence when SSE is unavailable. Contract: 1s. */
  pollMs?: number
}

export interface AuditFeed {
  entries: AuditEntry[]
  transport: Transport
  error: string | null
  loading: boolean
}

const merge = (cur: AuditEntry[], add: AuditEntry[]): AuditEntry[] => {
  if (!add.length) return cur
  const seen = new Set(cur.map((e) => e.seq))
  const fresh = add.filter((e) => !seen.has(e.seq) && seen.add(e.seq))
  if (!fresh.length) return cur
  return [...cur, ...fresh].sort((a, b) => a.seq - b.seq)
}

/**
 * Initial load, then SSE (/api/audit/stream) and, when SSE is missing or fails, 1s polling with after=seq.
 * Entries are deduplicated by seq so a stream/poll overlap never shows a row twice.
 */
export function useAuditFeed(client: AuditClient, { party, mission, pollMs = 1000 }: FeedOptions = {}): AuditFeed {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [transport, setTransport] = useState<Transport>('idle')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const last = useRef(0)

  useEffect(() => {
    let live = true
    let closeStream: (() => void) | null = null
    let timer: ReturnType<typeof setInterval> | null = null
    last.current = 0
    setEntries([])
    setLoading(true)

    const fetchAfter = (after: number) => (party ? client.party(party, after) : client.list({ mission, after }))
    const accept = (rows: AuditEntry[]) => {
      if (!live || !rows.length) return
      last.current = Math.max(last.current, ...rows.map((r) => r.seq))
      setEntries((cur) => merge(cur, rows))
    }
    const poll = async () => {
      try {
        accept(await fetchAfter(last.current))
        if (live) setError(null)
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : 'Audit feed failed')
      }
    }
    const startPolling = () => {
      if (!live || timer) return
      setTransport('polling')
      timer = setInterval(() => void poll(), pollMs)
    }

    ;(async () => {
      try {
        accept(await fetchAfter(0))
        if (live) setError(null)
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : 'Audit feed failed')
      }
      if (live) setLoading(false)
      if (!live) return
      closeStream = client.openStream({ party, mission, after: last.current }, accept, () => {
        closeStream = null
        startPolling()
      })
      if (closeStream) setTransport('sse')
      else startPolling()
    })()

    return () => {
      live = false
      closeStream?.()
      if (timer) clearInterval(timer)
    }
  }, [client, party, mission, pollMs])

  return { entries, transport, error, loading }
}
