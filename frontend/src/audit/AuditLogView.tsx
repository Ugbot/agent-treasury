import { PackButton } from '../pack'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ModeChip, VerdictChip } from '../components/ModeChip'
import { useAuditFeed } from './useAuditFeed'
import { verdictOf } from './types'
import type { AuditClient, AuditEntry, ChainStatus } from './types'

/** Which payment/operation an entry is about, if any: the why-paid drawer opens on it. */
export const whyTargetOf = (e: AuditEntry): string | null => {
  const p = e.payload
  const v = p.payment_id ?? p.paymentId ?? p.operation_id ?? p.operationId
  return typeof v === 'string' ? v : null
}

export function ChainBadge({ client, head }: { client: AuditClient; head: number }) {
  const [st, setSt] = useState<ChainStatus | 'error' | null>(null)
  const [busy, setBusy] = useState(false)
  const verify = useCallback(async () => {
    setBusy(true)
    try {
      setSt(await client.verify())
    } catch {
      setSt('error')
    } finally {
      setBusy(false)
    }
  }, [client])
  useEffect(() => {
    // Re-verify whenever the head moves so the badge never vouches for a stale chain.
    void verify()
  }, [verify, head])
  let cls = 'chain-pending'
  let text = 'Verifying chain...'
  if (st === 'error') { cls = 'chain-bad'; text = 'Chain not verified (verify endpoint unavailable)' }
  else if (st && st.ok) { cls = 'chain-ok'; text = `Hash chain verified, ${st.entries} entries` }
  else if (st) { cls = 'chain-bad'; text = `CHAIN BROKEN at entry #${st.broken_at_seq ?? '?'}` }
  return (
    <span className={`chain ${cls}`} role="status" data-testid="chain-badge">
      <span aria-hidden="true">{cls === 'chain-ok' ? '✓' : cls === 'chain-bad' ? '!' : '…'}</span> {text}
      {client.kind === 'fixture' && ' (fixture data)'}
      <button type="button" onClick={() => void verify()} disabled={busy}>Re-verify</button>
    </span>
  )
}

interface Filters { party: string; kind: string; actor: string; verdict: string; mode: string }
const NONE: Filters = { party: '', kind: '', actor: '', verdict: '', mode: '' }
const modeKey = (e: AuditEntry) => e.mode ?? 'none'

function Select({ id, label, value, options, onChange }: { id: string; label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">All</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  )
}

export function AuditLogView({ client, mission, highlightSeq, onWhy, pollMs }: {
  client: AuditClient
  mission?: string
  highlightSeq?: number | null
  onWhy?: (targetId: string, seq: number) => void
  pollMs?: number
}) {
  const feed = useAuditFeed(client, { mission, pollMs })
  const [f, setF] = useState<Filters>(NONE)
  const [openSeq, setOpenSeq] = useState<number | null>(highlightSeq ?? null)
  useEffect(() => {
    if (highlightSeq != null) setOpenSeq(highlightSeq)
  }, [highlightSeq])

  const opts = useMemo(() => {
    const u = (get: (e: AuditEntry) => string | null | undefined) =>
      [...new Set(feed.entries.map(get).filter((x): x is string => !!x))].sort()
    return { party: u((e) => e.party), kind: u((e) => e.kind), actor: u((e) => e.actor), verdict: u(verdictOf), mode: u(modeKey) }
  }, [feed.entries])
  const shown = useMemo(
    () => feed.entries.filter((e) =>
      (!f.party || e.party === f.party) && (!f.kind || e.kind === f.kind) && (!f.actor || e.actor === f.actor) &&
      (!f.verdict || verdictOf(e) === f.verdict) && (!f.mode || modeKey(e) === f.mode)),
    [feed.entries, f],
  )
  const head = feed.entries.length ? feed.entries[feed.entries.length - 1].seq : 0
  const set = (k: keyof Filters) => (v: string) => setF((c) => ({ ...c, [k]: v }))

  return (
    <section className="panel auditlog" aria-labelledby="audit-h">
      <div className="head">
        <h2 id="audit-h">Everything log</h2>
        <ChainBadge client={client} head={head} />
        <span className="hint">via {feed.transport === 'sse' ? 'live stream (SSE)' : feed.transport === 'polling' ? 'polling every second' : '...'}</span>
      </div>
      <p className="hint">Append-only. Every input, decision, agent proposal and verdict, Stripe call and approval, in order. Each entry is chained to the one before it.</p>
      {feed.error && <div role="alert" className="error inline">{feed.error}</div>}
      <div className="filters" role="group" aria-label="Filters">
        <Select id="f-party" label="Party" value={f.party} options={opts.party} onChange={set('party')} />
        <Select id="f-kind" label="Kind" value={f.kind} options={opts.kind} onChange={set('kind')} />
        <Select id="f-actor" label="Actor" value={f.actor} options={opts.actor} onChange={set('actor')} />
        <Select id="f-verdict" label="Verdict" value={f.verdict} options={opts.verdict} onChange={set('verdict')} />
        <Select id="f-mode" label="Mode" value={f.mode} options={opts.mode} onChange={set('mode')} />
        <button type="button" onClick={() => setF(NONE)}>Clear</button>
        {client.kind === 'http' && <a className="btnlink" href="/api/audit?format=ndjson" download="audit.ndjson">Export NDJSON</a>}
        {client.kind === 'http' && mission && <PackButton mission={mission} />}
      </div>
      <p className="hint" aria-live="polite">{shown.length} of {feed.entries.length} entries</p>
      {feed.loading ? <p role="status">Loading...</p> : shown.length === 0 ? <p>No entries match.</p> : (
        <ol className="log">
          {shown.map((e) => {
            const target = whyTargetOf(e)
            const open = openSeq === e.seq
            return (
              <li key={e.seq} id={`audit-${e.seq}`} className={open ? 'hl' : ''} data-seq={e.seq}>
                <div className="line">
                  <span className="seq">#{e.seq}</span>
                  <time className="t" dateTime={e.ts}>{e.ts.slice(11, 19)}</time>
                  <span className="actor">{e.actor}</span>
                  {e.party && <span className="badge party">{e.party}</span>}
                  <span className="kind">{e.kind}</span>
                  <VerdictChip verdict={verdictOf(e)} />
                  <ModeChip mode={e.mode} />
                </div>
                <div className="sum">{e.summary}</div>
                <div className="row2">
                  <button type="button" className="link" aria-expanded={open} onClick={() => setOpenSeq(open ? null : e.seq)}>{open ? 'Hide detail' : 'Detail'}</button>
                  {target && onWhy && <button type="button" className="link" onClick={() => onWhy(target, e.seq)}>Why was this paid?</button>}
                </div>
                {open && (
                  <dl className="detail">
                    {e.mission_id && (<><dt>mission</dt><dd>{e.mission_id}</dd></>)}
                    {e.policy && (<><dt>policy</dt><dd><code>{JSON.stringify(e.policy)}</code></dd></>)}
                    {e.stripe && (<><dt>stripe</dt><dd><code>{JSON.stringify(e.stripe)}</code></dd></>)}
                    <dt>payload</dt><dd><code>{JSON.stringify(e.payload)}</code></dd>
                    <dt>prev hash</dt><dd><code>{e.prev_hash}</code></dd>
                    <dt>hash</dt><dd><code>{e.hash}</code></dd>
                  </dl>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
