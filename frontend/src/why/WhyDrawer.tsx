import { PackButton } from '../pack'
import { useEffect, useRef, useState } from 'react'
import { ModeChip, VerdictChip } from '../components/ModeChip'
import { errMsg } from '../lib/wire'
import type { Explanation, WhyClient } from './types'

const SOURCE: Record<Explanation['source'], string> = {
  cognee: 'Retrieved from Cognee memory',
  audit_log: 'From the audit log (canonical record)',
  unknown: 'Source not reported by the server',
}

export function WhyDrawer({ client, paymentId, onClose, onOpenAudit, onOpenHistory }: {
  client: WhyClient
  paymentId: string
  onClose: () => void
  onOpenAudit?: (seq: number) => void
  onOpenHistory?: (paymentId: string) => void
}) {
  const [ex, setEx] = useState<Explanation | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    let live = true
    setEx(null)
    setErr(null)
    client.explain(paymentId).then((e) => live && setEx(e), (e) => live && setErr(errMsg(e)))
    return () => { live = false }
  }, [client, paymentId])
  useEffect(() => {
    closeRef.current?.focus()
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])

  return (
    <div className="scrim" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="why-h" onClick={(e) => e.stopPropagation()}>
        <div className="head">
          <h2 id="why-h">Why was this paid?</h2>
          <button type="button" ref={closeRef} onClick={onClose} aria-label="Close explanation">Close</button>
        </div>
        <p className="hint">Payment <code>{paymentId}</code></p>
        <PackButton mission={paymentId} />
        {onOpenHistory && <button type="button" className="link" onClick={() => onOpenHistory(paymentId)}>View history (policy then vs now)</button>}
        {!ex && !err && <p role="status">Loading explanation...</p>}
        {err && <div role="alert" className="error inline">Could not load an explanation: {err}</div>}
        {ex && (
          <>
            <div className="why-top">
              <VerdictChip verdict={ex.verdict} />
              <ModeChip mode={ex.mode} />
              {ex.livemode === true && <span className="badge v-deny">LIVEMODE: REJECTED</span>}
            </div>
            {ex.text && <p className="why-text">{ex.text}</p>}
            <p className={`source source-${ex.source}`} data-testid="why-source">
              <strong>{SOURCE[ex.source]}.</strong> {ex.sourceNote ?? ''}
            </p>

            <h3>Policy</h3>
            <dl>
              <dt>policy</dt><dd>{ex.policy.id ?? 'not reported'}{ex.policy.version != null ? ` v${ex.policy.version}` : ''}</dd>
              <dt>hash</dt><dd><code>{ex.policy.hash ?? 'not reported'}</code></dd>
            </dl>

            <h3>Clauses</h3>
            {ex.clauses.length === 0 ? <p>No clause results reported.</p> : (
              <ul className="clauses">
                {ex.clauses.map((c, i) => (
                  <li key={`${c.policyId}-${c.clauseId}-${i}`} className={c.passed === false ? 'fail' : c.passed ? 'pass' : ''}>
                    <strong>{c.clauseId}</strong> <span className="mark">{c.passed === false ? 'did not pass' : c.passed ? 'passed' : c.outcome ?? 'n/a'}</span>
                    {c.outcome && c.outcome !== 'allow' && <> <VerdictChip verdict={c.outcome} /></>}
                    {c.prose && <div>{c.prose}</div>}
                    {(c.observed || c.expected) && <div className="hint">observed {c.observed ?? '?'}, expected {c.expected ?? '?'}</div>}
                    {c.evidence != null && c.evidence !== '' && <div className="hint"><code>{typeof c.evidence === 'string' ? c.evidence : JSON.stringify(c.evidence)}</code></div>}
                    {c.policyId && <div className="hint">{c.policyId}{c.version != null ? ` v${c.version}` : ''}</div>}
                  </li>
                ))}
              </ul>
            )}

            <h3>Evidence</h3>
            {ex.evidence.length === 0 ? <p>No evidence items reported.</p> : (
              <dl>{ex.evidence.map((e, i) => (<span key={i} className="kv"><dt>{e.label}</dt><dd>{e.value}</dd></span>))}</dl>
            )}

            <h3>Stripe objects</h3>
            {ex.stripeIds.length === 0 ? <p>No Stripe object. {ex.mode === 'mock' || ex.mode === 'fixture' ? 'This payment was simulated.' : 'None reported.'}</p> : (
              <ul className="ids">{ex.stripeIds.map((id) => <li key={id}><code>{id}</code></li>)}</ul>
            )}

            <h3>Audit entries</h3>
            {ex.auditSeqs.length === 0 ? <p>No audit links reported.</p> : (
              <p className="links">
                {ex.auditSeqs.map((s) => (
                  <button key={s} type="button" className="link" onClick={() => onOpenAudit?.(s)}>#{s}</button>
                ))}
              </p>
            )}
          </>
        )}
      </aside>
    </div>
  )
}
