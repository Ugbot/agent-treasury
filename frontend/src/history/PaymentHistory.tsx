import { useEffect, useMemo, useRef, useState } from 'react'
import { VerdictChip } from '../components/ModeChip'
import { errMsg } from '../lib/wire'
import type { HistoryClient, HistoryItem, PaymentHistoryData } from './types'

const t = (iso: string) => (iso ? iso.replace('T', ' ').replace(/\.\d+/, '').replace(/\+00:00$|Z$/, ' UTC') : 'unknown')

export function DivergenceBanner({ d }: { d: NonNullable<PaymentHistoryData['divergence']> }) {
  const diverged = d.recordedVerdict !== d.wouldNow
  return (
    <div role={diverged ? 'alert' : 'status'} className={`hist-banner ${diverged ? 'diverged' : 'same'}`} data-testid="divergence">
      <strong>{diverged ? `${d.allowedThen ? 'Allowed' : d.recordedVerdict} then, would ${d.wouldNow} now` : `Same verdict now (${d.recordedVerdict})`}</strong>
      {diverged && d.differing.length > 0 && (
        <ul>{d.differing.map((c) => <li key={`${c.policyId}${c.clause}`}><code>{c.clause}</code>: {c.then ?? '-'} then, {c.now ?? '-'} now. {c.why}</li>)}</ul>
      )}
      <p>{d.summary}</p>
    </div>
  )
}

function Detail({ item }: { item: HistoryItem }) {
  return (
    <section aria-label={`Event ${item.seq}`} className="hist-detail">
      <h3>#{item.seq} {item.kind} {item.verdict && <VerdictChip verdict={item.verdict} />}</h3>
      <p>{item.summary}</p>
      <dl>
        <dt>Happened at (valid time)</dt>
        <dd>{t(item.validAt)}{item.validTimeSource === 'recorded_time' ? ' (not reported; shown as recorded time)' : ''}</dd>
        <dt>Recorded at (transaction time)</dt><dd>{t(item.recordedAt)}</dd>
        <dt>Policy in force</dt>
        <dd>
          {item.inForce.policies.length === 0 ? 'none' : item.inForce.policies.map((p) => (
            <span key={p.policyId + p.version} className="hist-pol">{p.policyId} v{p.version ?? '?'} <code>{p.hash.slice(0, 10)}</code>{p.mandatory ? ' (mandatory)' : ''}</span>
          ))}
          {item.inForce.effectiveHash && <> effective <code>{item.inForce.effectiveHash.slice(0, 12)}</code></>}
        </dd>
      </dl>
      {item.lateArrival && <p className="hist-late">Late arrival: recorded after later-happening events. History was not rewritten.</p>}
      {item.supersedes != null && <p>Corrects entry #{item.supersedes}.</p>}
      {item.clauses.length > 0 && <ul aria-label="Clause results">{item.clauses.map((c) => <li key={c.clause}><code>{c.clause}</code> {c.outcome}</li>)}</ul>}
      {item.reproduced !== null && <p>{item.reproduced ? 'Re-evaluation reproduces the recorded outcome.' : 'Re-evaluation does NOT reproduce the recorded outcome.'}</p>}
      {item.divergence && <DivergenceBanner d={item.divergence} />}
    </section>
  )
}

export function PaymentHistory({ client, paymentId, onClose }: { client: HistoryClient; paymentId: string; onClose?: () => void }) {
  const [data, setData] = useState<PaymentHistoryData | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [order, setOrder] = useState<'valid' | 'recorded'>('valid')
  const [idx, setIdx] = useState(0)
  const first = useRef(true)

  useEffect(() => {
    let live = true
    setData(null); setErr(null)
    client.payment(paymentId).then((d) => { if (live) { setData(d); setIdx(Math.max(0, d.items.length - 1)) } }, (e) => live && setErr(errMsg(e)))
    return () => { live = false }
  }, [client, paymentId])
  useEffect(() => { first.current = false }, [])

  const items = useMemo(() => {
    const xs = [...(data?.items ?? [])]
    return order === 'valid' ? xs.sort((a, b) => Date.parse(a.validAt) - Date.parse(b.validAt) || a.seq - b.seq) : xs.sort((a, b) => a.seq - b.seq)
  }, [data, order])
  const cur = items[Math.min(idx, items.length - 1)]

  return (
    <div className="page hist" aria-labelledby="hist-h">
      <div className="head">
        <h2 id="hist-h">History of {paymentId}</h2>
        {onClose && <button type="button" onClick={onClose}>Close history</button>}
      </div>
      {!data && !err && <p role="status">Loading history...</p>}
      {err && <div role="alert" className="error inline">Could not load history: {err}</div>}
      {data && (
        <>
          {client.kind === 'fixture' && <p className="banner" role="note">Fixture data, not from the audit log</p>}
          <p className="hint">{data.summary}</p>
          {data.divergence && <DivergenceBanner d={data.divergence} />}
          {items.length === 0 ? <p>No recorded events for this payment.</p> : (
            <>
              <div role="group" aria-label="Time axis">
                <button type="button" aria-pressed={order === 'valid'} onClick={() => setOrder('valid')}>By when it happened</button>
                <button type="button" aria-pressed={order === 'recorded'} onClick={() => setOrder('recorded')}>By when it was recorded</button>
              </div>
              <label>Scrub through time
                <input type="range" min={0} max={items.length - 1} value={Math.min(idx, items.length - 1)} onChange={(e) => setIdx(Number(e.target.value))} aria-valuetext={cur ? `event ${cur.seq}` : ''} />
              </label>
              <ol className="hist-line">
                {items.map((it, i) => (
                  <li key={it.seq}>
                    <button type="button" aria-current={i === idx} onClick={() => setIdx(i)} className={it.lateArrival ? 'late' : ''}>
                      #{it.seq} {it.kind}{it.lateArrival ? ' LATE' : ''} <small>{t(order === 'valid' ? it.validAt : it.recordedAt)}</small>
                    </button>
                  </li>
                ))}
              </ol>
              {cur && <Detail item={cur} />}
            </>
          )}
        </>
      )}
    </div>
  )
}
