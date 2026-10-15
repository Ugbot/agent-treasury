import type { Snapshot } from '../types'
import { ModeBadge, money } from './ModeBadge'

const VERDICT_TEXT = { allow: 'ALLOW', hold: 'HOLD', deny: 'DENY', approval_required: 'APPROVAL REQUIRED' } as const

export function Timeline({ s }: { s: Snapshot }) {
  const d = s.decision
  const failed = d?.clauses.filter((c) => !c.passed) ?? []
  return (
    <section aria-labelledby="flow-h" className="panel">
      <h2 id="flow-h">Input to payment</h2>
      <ol className="flow">
        <li>
          <h3>Input</h3>
          <dl>
            <dt>Amount</dt><dd>{money(s.inputs.amountMinor, s.inputs.currency)}</dd>
            <dt>Insurance</dt><dd>{s.inputs.insured ? 'covered' : 'not covered'}</dd>
            <dt>Location</dt><dd>{s.inputs.location}</dd>
            <dt>Receipt</dt><dd>{s.inputs.receiptConfirmed ? 'confirmed' : 'missing'}</dd>
          </dl>
        </li>
        <li>
          <h3>Policy</h3>
          <ul className="plain">
            {s.policies.map((p) => <li key={p.policyId}>{p.policyId} v{p.version}{p.mandatory ? ' (mandatory)' : ''}</li>)}
          </ul>
          {d && <p className="hint">hash {d.effectivePolicyHash}</p>}
        </li>
        <li>
          <h3>Decision</h3>
          {d ? (
            <>
              <p className={`verdict v-${d.verdict}`} role="status">{VERDICT_TEXT[d.verdict]}</p>
              {failed.length > 0 && <ul className="plain">{failed.map((c) => <li key={c.clauseId}>{c.clauseId}: {c.prose} (saw {c.observed}, need {c.expected})</li>)}</ul>}
              {d.obligations.length > 0 && (
                <>
                  <h4>Pending obligations</h4>
                  <ul className="plain">{d.obligations.map((o) => <li key={o.obligationId}>{o.kind} - {o.status}</li>)}</ul>
                </>
              )}
            </>
          ) : <p>No decision yet.</p>}
        </li>
        <li>
          <h3>Payment</h3>
          {s.operation ? (
            <p>{money(s.operation.amountMinor, s.operation.currency)} {s.operation.status} <ModeBadge mode={s.operation.mode} /></p>
          ) : <p>No payment made.</p>}
        </li>
      </ol>
    </section>
  )
}
