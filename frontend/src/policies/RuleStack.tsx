import type { ReactNode } from 'react'
import { clauseSentence, clauseVerdict, evidenceSentence, VERDICT_LABEL } from './clauses'
import type { Layer } from './layers'
import type { ClauseOutcome, Outcome, WireClause } from './types'

export type ResultMap = Map<string, ClauseOutcome>
export const resultKey = (policyId: string, clauseId: string) => `${policyId}:${clauseId}`

const GLYPH: Record<Outcome, string> = { allow: '✓', hold: '!', deny: '×' }
const PASS_WORD: Record<Outcome, string> = { allow: 'Met', hold: 'Not met, holds', deny: 'Not met, denies' }

function Rule({ clause, result }: { clause: WireClause; result?: ClauseOutcome }) {
  const v = clauseVerdict(clause)
  const out = result?.outcome
  return (
    <li className="pol-rule" data-outcome={out ?? 'none'} data-deciding={out && out !== 'allow' ? 'true' : undefined}>
      {out ? (
        <span className={`pol-mark m-${out}`} aria-hidden="true">{GLYPH[out]}</span>
      ) : (
        <span className="pol-mark m-none" aria-hidden="true" />
      )}
      <div className="pol-rule-body">
        <p className="pol-sentence">{clauseSentence(clause)}</p>
        <p className="pol-rule-meta">
          <code className="pol-tag">{clause.id}</code>
          <span className={`pol-chip v-${v}`}>{VERDICT_LABEL[v]} if not met</span>
        </p>
        {result && result.outcome === 'allow' && <span className="sr-only">Met</span>}
        {result && result.outcome !== 'allow' && <p className="pol-evidence"><span className="sr-only">{PASS_WORD[result.outcome]}. </span>{evidenceSentence(clause, result)}</p>}
      </div>
    </li>
  )
}

interface Props {
  layers: Layer[]
  results: ResultMap | null
  selectedPolicyId: string | null
  onSelect: (policyId: string) => void
  paymentLabel: string
  verdictSlot: ReactNode
  totalRules: number
}

/** The one visual: four layers in resolution order, each adding rules, ending in the verdict. */
export function RuleStack({ layers, results, selectedPolicyId, onSelect, paymentLabel, verdictSlot, totalRules }: Props) {
  return (
    <ol className="pol-stack" aria-label="Rule stack, from organisation down to this payment">
      {layers.map((l, i) => (
        <li key={l.type} className="pol-layer" data-layer={l.type} data-empty={l.policies.length === 0 ? 'true' : undefined}>
          <span className="pol-node" aria-hidden="true">{i + 1}</span>
          <header className="pol-layer-head">
            <h3>{l.title}{l.type === 'organisation' && <span className="pol-lock">Locked</span>}</h3>
            <p className="pol-layer-count">
              {l.adds > 0 ? <>Adds {l.adds} {l.adds === 1 ? 'rule' : 'rules'}, <strong>{l.inForce} in force</strong></> : <>Adds nothing, <strong>{l.inForce} in force</strong></>}
            </p>
          </header>
          <p className="pol-layer-blurb">{l.type === 'payment' ? `Added for ${paymentLabel} only.` : l.blurb}</p>
          {l.policies.length === 0 ? (
            <p className="pol-none">{l.type === 'organisation' ? 'No organisation policy is attached.' : 'Nothing added. Pick a policy in the library to add rules.'}</p>
          ) : (
            l.policies.map(({ attachment: a, bundle: b }) => (
              <div key={a.attachment_id} className="pol-policy" data-selected={selectedPolicyId === a.policy_id ? 'true' : undefined}>
                <button type="button" className="pol-policy-name" aria-pressed={selectedPolicyId === a.policy_id} onClick={() => onSelect(a.policy_id)} aria-label={`${b?.title ?? a.policy_id} version ${a.policy_version}, ${a.mandatory ? 'required' : 'attached'}. Show details`}>
                  <span>{b?.title ?? a.policy_id}</span>
                  <span className="pol-ver">v{a.policy_version}{a.mandatory ? ', required' : ''}</span>
                </button>
                {b ? (
                  <ul className="pol-rules">
                    {b.clauses.map((c) => <Rule key={c.id} clause={c} result={results?.get(resultKey(a.policy_id, c.id))} />)}
                  </ul>
                ) : <p className="pol-none">Policy content is not available.</p>}
              </div>
            ))
          )}
        </li>
      ))}
      <li className="pol-combine" aria-label="How rules combine">
        <p><strong>Strictest wins: deny beats hold beats allow.</strong> Layers only add rules, so an attached policy can make a decision stricter and never looser.</p>
        <p className="pol-total">{totalRules} {totalRules === 1 ? 'rule' : 'rules'} in force for {paymentLabel}</p>
      </li>
      <li className="pol-layer pol-final">
        <span className="pol-node pol-node-final" aria-hidden="true">=</span>
        {verdictSlot}
      </li>
    </ol>
  )
}
