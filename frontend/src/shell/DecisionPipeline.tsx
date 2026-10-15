import type { ReactNode } from 'react'
import { ModeBadge } from '../components/ModeBadge'
import { formatMoney } from '../lib/wire'
import type { ClauseResult, Snapshot, Verdict } from '../types'
import { inputRows, pipelineStates, plainObserved, policyName } from './status'
import type { StageState } from './status'

const GLYPH: Record<StageState, string> = { idle: '–', ok: '✓', wait: '!', stop: '×' }
const WORD: Record<StageState, string> = { idle: 'Not started', ok: 'Met', wait: 'Waiting', stop: 'Stopped' }
const VERDICT_WORD: Record<Verdict, string> = { allow: 'Allow', hold: 'Hold', deny: 'Deny', approval_required: 'Needs approval' }

function Stage({ n, title, sub, state, children }: { n: number; title: string; sub: string; state: StageState; children: ReactNode }) {
  return (
    <li className="stage" data-state={state} aria-label={`Step ${n}, ${title}: ${WORD[state]}`}>
      <div className="stage-head">
        <span className="node" aria-hidden="true">{GLYPH[state]}</span>
        <div>
          <h3>{title}</h3>
          <p className="stage-sub">{sub}</p>
        </div>
        <span className="sr-only">{WORD[state]}</span>
      </div>
      <div className="stage-body">{children}</div>
    </li>
  )
}

function Tick({ ok }: { ok: boolean }) {
  return <span className={`tick ${ok ? 'tick-ok' : 'tick-bad'}`} aria-hidden="true">{ok ? '✓' : '×'}</span>
}

function ClauseRow({ c }: { c: ClauseResult }) {
  return (
    <li className={c.passed ? 'clause pass' : 'clause fail'}>
      <Tick ok={c.passed} />
      <div>
        <p className="clause-line"><code className="rule-id">{c.clauseId}</code> {c.prose}</p>
        {!c.passed && <p className="clause-why"><span className="sr-only">Result: </span>Saw {plainObserved(c.observed)}; needs {c.expected.replace(/_/g, ' ')}.</p>}
        <span className="sr-only">{c.passed ? 'Passed' : 'Did not pass'}</span>
      </div>
    </li>
  )
}

/** Inputs, chained policies, decision, money. Each stage lights up from the snapshot as the mission progresses. */
export function DecisionPipeline({ s }: { s: Snapshot | null }) {
  const st = pipelineStates(s)
  const d = s?.decision ?? null
  const op = s?.operation ?? null
  const rows = s ? inputRows(s) : []
  const sig = d ? `${d.decisionId}:${d.verdict}:${op?.status ?? ''}` : 'none'
  return (
    <section className="pipeline-wrap" aria-labelledby="pipe-h">
      <h2 id="pipe-h" className="sr-only">Decision pipeline</h2>
      <ol className="pipeline">
        <Stage n={1} title="Inputs" sub="What we know right now" state={st.inputs}>
          {s ? (
            <ul className="checks">
              {rows.map((r) => (
                <li key={r.key}>
                  <Tick ok={r.met} />
                  <span className="check-label">{r.label}</span>
                  <span className="check-value">{r.value}{r.note ? `, ${r.note}` : ''}</span>
                  {r.simulated && <span className="sim" title="Simulated for this demo: not read from a real system">simulated</span>}
                </li>
              ))}
            </ul>
          ) : <p className="empty">No mission loaded.</p>}
        </Stage>

        <Stage n={2} title="Policies" sub="Written rules, applied in order" state={st.policies}>
          {s && s.policies.length > 0 && (
            <ol className="chain-list" aria-label="Policy chain">
              {s.policies.map((p, i) => (
                <li key={`${p.policyId}-${i}`}>
                  <span className="chain-name">{policyName(p.policyId)}</span>
                  <span className="chain-meta">v{p.version}{p.mandatory ? ', required' : ', attached'}</span>
                </li>
              ))}
            </ol>
          )}
          {d ? (
            <>
              <ul className="clauses-plain">{d.clauses.map((c) => <ClauseRow key={c.clauseId} c={c} />)}</ul>
              <p className="hash">Policy chain hash <code>{d.effectivePolicyHash}</code></p>
            </>
          ) : <p className="empty">No rules have run. They run as soon as a payment is requested.</p>}
        </Stage>

        <Stage n={3} title="Decision" sub="Allow, hold or deny" state={st.decision}>
          {d ? (
            <>
              <p key={sig} className={`verdict-word v-${d.verdict}`}>{VERDICT_WORD[d.verdict]}</p>
              <p className="decision-line">
                {d.verdict === 'allow' && 'Every rule passed.'}
                {d.verdict === 'hold' && 'At least one rule is waiting on something. Nothing is paid yet.'}
                {d.verdict === 'approval_required' && 'A person has to approve before anything is paid.'}
                {d.verdict === 'deny' && 'A rule forbids this payment. It will not be paid.'}
              </p>
              {d.obligations.length > 0 && (
                <ul className="plain">{d.obligations.map((o) => <li key={o.obligationId}>{o.kind.replace(/_/g, ' ')}: {o.status}</li>)}</ul>
              )}
            </>
          ) : <p className="empty">No decision yet.</p>}
        </Stage>

        <Stage n={4} title="Money" sub="Only after the decision" state={st.money}>
          {op ? (
            <>
              <p key={sig} className="money-amount">{formatMoney(op.amountMinor, op.currency)}</p>
              <p className="money-status">{op.status.replace(/_/g, ' ')}</p>
              <p><ModeBadge mode={op.mode} /></p>
              <p className="stripe-id">{op.providerObjectId ? <>Stripe object <code>{op.providerObjectId}</code></> : op.mode === 'stripe_test' ? 'Stripe object id not reported yet.' : 'No Stripe object: this payment is simulated.'}</p>
            </>
          ) : (
            <>
              <p className={`money-none ${st.money === 'stop' ? 'stopped' : ''}`}>{st.money === 'stop' ? 'No money moved.' : d ? 'Not paid yet.' : 'Nothing to pay yet.'}</p>
              {s && <p className="hint">{s.mode === 'stripe_test' ? 'Payments will create Stripe test objects.' : s.mode === 'mock' ? 'Mock mode: payments are simulated, Stripe is not called.' : 'Fixture mode: canned data only.'}</p>}
              {s && <p><ModeBadge mode={s.mode} /></p>}
            </>
          )}
        </Stage>
      </ol>
    </section>
  )
}
