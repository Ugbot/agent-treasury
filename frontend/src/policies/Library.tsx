import { clauseSentence, clauseVerdict, summarise, VERDICT_LABEL } from './clauses'
import { LAYERS } from './layers'
import type { Attachment, PolicyBundle, TargetRef, TargetType } from './types'

export const ATTACHABLE: { type: TargetType; label: string }[] = [
  { type: 'payment', label: 'This payment' },
  { type: 'card', label: 'Card' },
  { type: 'mission', label: 'Job' },
]
const layerTitle = (t: TargetType) => LAYERS.find((l) => l.type === t)?.title ?? t
const noun = (t: TargetType) => (t === 'mission' ? 'job' : t)

export function isMandatory(policyId: string, attachments: Attachment[]) {
  return attachments.some((a) => a.policy_id === policyId && a.mandatory)
}

interface ListProps {
  bundles: PolicyBundle[]
  attachments: Attachment[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export function LibraryList({ bundles, attachments, selectedId, onSelect }: ListProps) {
  if (bundles.length === 0) return <p className="empty">No policies are published yet. Publish one through the API and it will appear here.</p>
  return (
    <ul className="pol-lib" aria-label="Policy library">
      {bundles.map((b) => {
        const m = isMandatory(b.policy_id, attachments)
        return (
          <li key={`${b.policy_id}@${b.version}`}>
            <button type="button" className="pol-lib-item" aria-pressed={selectedId === b.policy_id} onClick={() => onSelect(b.policy_id)}>
              <span className="pol-lib-top">
                <span className="pol-lib-name">{b.title}</span>
                <span className="pol-ver">v{b.version}</span>
                <span className={`pol-badge ${m ? 'req' : 'opt'}`}>{m ? 'Required' : 'Optional'}</span>
              </span>
              <span className="pol-lib-sum">{summarise(b.clauses)}</span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

interface DetailProps {
  bundle: PolicyBundle
  attachments: Attachment[]
  chain: TargetRef[]
  target: TargetType
  onTarget: (t: TargetType) => void
  busy: boolean
  onAttach: () => void
  onDetach: (a: Attachment) => void
}

export function LibraryDetail({ bundle, attachments, chain, target, onTarget, busy, onAttach, onDetach }: DetailProps) {
  const mine = attachments.filter((a) => a.policy_id === bundle.policy_id)
  const mandatory = mine.some((a) => a.mandatory)
  const ref = chain.find((t) => t.type === target)!
  const already = mine.some((a) => a.target_type === target && a.target_id === ref.id)
  const n = bundle.clauses.length
  const where = `${noun(target)} ${ref.id}`
  const reason = mandatory
    ? 'Required by the organisation, so it already applies to every payment.'
    : already
      ? `Already attached to ${where}.`
      : null
  return (
    <section className="pol-detail" aria-labelledby="pol-detail-h" data-testid="selected-policy">
      <h3 id="pol-detail-h">{bundle.title}</h3>
      <p className="pol-detail-meta">
        Version {bundle.version}, fingerprint <code title={bundle.content_hash}>{bundle.content_hash.slice(0, 10)}</code>
        <span className={`pol-badge ${mandatory ? 'req' : 'opt'}`}>{mandatory ? 'Required' : 'Optional'}</span>
      </p>
      <ul className="pol-rules">
        {bundle.clauses.map((c) => (
          <li key={c.id} className="pol-rule" data-outcome="none">
            <span className="pol-mark m-none" aria-hidden="true" />
            <div className="pol-rule-body">
              <p className="pol-sentence">{clauseSentence(c)}</p>
              <p className="pol-rule-meta"><code className="pol-tag">{c.id}</code><span className={`pol-chip v-${clauseVerdict(c)}`}>{VERDICT_LABEL[clauseVerdict(c)]} if not met</span></p>
            </div>
          </li>
        ))}
      </ul>
      <details className="pol-tech">
        <summary>Technical detail</summary>
        <pre>{JSON.stringify(bundle.clauses.map((c) => ({ id: c.id, kind: c.kind, params: c.params })), null, 2)}</pre>
      </details>

      <div className="pol-attach">
        <label htmlFor="pol-target">Attach to</label>
        <select id="pol-target" value={target} onChange={(e) => onTarget(e.target.value as TargetType)} disabled={mandatory}>
          {ATTACHABLE.map((o) => <option key={o.type} value={o.type}>{o.label}</option>)}
        </select>
        <p className="pol-attach-note" id="pol-attach-note">
          {reason ?? `This would add ${n} ${n === 1 ? 'rule' : 'rules'} to ${where} and can only make decisions stricter.`}
        </p>
        <button type="button" className="primary" onClick={onAttach} disabled={busy || !!reason} aria-describedby="pol-attach-note">Attach to {target === 'payment' ? 'this payment' : noun(target)}</button>
      </div>

      {mine.length > 0 && (
        <div className="pol-where">
          <h4>Where it applies</h4>
          <ul>
            {mine.map((a) => (
              <li key={a.attachment_id}>
                <span>{layerTitle(a.target_type)}</span>
                <button type="button" onClick={() => onDetach(a)} disabled={busy || a.mandatory} aria-describedby={a.mandatory ? `pol-why-${a.attachment_id}` : undefined}>Detach from {layerTitle(a.target_type).toLowerCase()}</button>
                {a.mandatory && <span className="pol-why" id={`pol-why-${a.attachment_id}`}>Required by the organisation, it cannot be removed.</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
