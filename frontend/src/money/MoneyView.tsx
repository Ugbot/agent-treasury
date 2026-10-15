import { useState } from 'react'
import { KIND_LABEL, fixtureMoney, flowClass, flowSymbol, gbp, netMinor, redact, statusLabel } from './model'
import type { Allowance, Flow, MoneyData, MoneyMode } from './model'

const MODE_TEXT: Record<MoneyMode, string> = { fixture: 'FIXTURE', mock: 'MOCK', stripe_test: 'STRIPE TEST' }

function Mode({ mode, livemode }: { mode: MoneyMode; livemode: boolean }) {
  return (
    <span className={`badge mode-${mode}`}>
      {MODE_TEXT[mode]} {livemode ? 'livemode=true' : 'livemode=false'}
    </span>
  )
}

export function AllowanceGauge({ a }: { a: Allowance }) {
  const free = Math.max(a.limitMinor - a.reservedMinor - a.capturedMinor, 0)
  const pct = (n: number) => `${a.limitMinor > 0 ? Math.min((n / a.limitMinor) * 100, 100) : 0}%`
  return (
    <div className="gauge">
      <h4>{a.label}</h4>
      <div
        className="gauge-bar"
        role="meter"
        aria-label={`${a.label} used`}
        aria-valuemin={0}
        aria-valuemax={a.limitMinor}
        aria-valuenow={a.reservedMinor + a.capturedMinor}
        aria-valuetext={`${gbp(a.reservedMinor + a.capturedMinor)} of ${gbp(a.limitMinor)}`}
      >
        <span className="seg captured" style={{ width: pct(a.capturedMinor) }} />
        <span className="seg reserved" style={{ width: pct(a.reservedMinor) }} />
      </div>
      <p className="hint">Captured {gbp(a.capturedMinor)}, reserved {gbp(a.reservedMinor)}, free {gbp(free)} of {gbp(a.limitMinor)}</p>
    </div>
  )
}

function Inspector({ f }: { f: Flow }) {
  const e = f.evidence
  return (
    <section aria-label="Evidence inspector" className="panel inspector">
      <h3>Why {f.verdict === 'allow' ? 'paid' : f.verdict === 'hold' ? 'held' : 'denied'}: {KIND_LABEL[f.kind]}</h3>
      <p>{statusLabel(f)}</p>
      <dl>
        <dt>Policy</dt><dd>{e.policy ? `${e.policy.policyId} v${e.policy.version} (hash ${e.policy.hash})` : 'none'}</dd>
        <dt>Attachments</dt><dd>{f.attachments.join(', ') || 'none'}</dd>
        <dt>Input snapshot</dt>
        <dd>{Object.entries(e.inputSnapshot).map(([k, v]) => `${k}=${String(v)}`).join(', ')}</dd>
        <dt>Approvals</dt><dd>{e.approvals.join(', ') || 'none'}</dd>
        <dt>Provider receipt</dt>
        <dd>{e.receipt ? redact(`${e.receipt.objectType} ${e.receipt.objectId} (${e.receipt.status})`) : 'No provider receipt'}</dd>
        <dt>Lineage</dt><dd>plan {e.lineage.planId}; saga {e.lineage.sagaSteps.join(' > ') || 'none'}</dd>
      </dl>
      <h4>Clauses</h4>
      <ul className="plain">
        {e.clauses.map((c) => <li key={c.clauseId}>{c.clauseId} v{c.version}: {c.prose} - {c.passed ? 'passed' : 'failed'}</li>)}
      </ul>
    </section>
  )
}

export function MoneyView({ data }: { data?: MoneyData }) {
  const d = data ?? fixtureMoney()
  const [sel, setSel] = useState<string | null>(null)
  const selected = d.flows.find((f) => f.id === sel) ?? null
  return (
    <section aria-labelledby="money-h" className="panel money">
      <h2 id="money-h">Money flows</h2>
      {!data && <p className="banner" role="note">Fixture data: illustrative amounts, no backend</p>}
      <div className="funds" aria-label="Funds">
        <p><strong>Pending (not yet spendable)</strong> {gbp(d.funds.pendingMinor)}</p>
        <p><strong>Available (spendable)</strong> {gbp(d.funds.availableMinor)}</p>
        <p><strong>In transit to bank</strong> {gbp(d.funds.inTransitToBankMinor)}</p>
      </div>
      <h3>Allowances</h3>
      <div className="gauges">{d.allowances.map((a) => <AllowanceGauge key={a.id} a={a} />)}</div>
      <h3>Flows</h3>
      <ul className="plain flows">
        {d.flows.map((f) => {
          const c = flowClass(f)
          return (
            <li key={f.id} className={`flow-row flow-${c}`}>
              <button type="button" aria-pressed={sel === f.id} onClick={() => setSel(f.id === sel ? null : f.id)}>
                <span aria-hidden="true">{flowSymbol(c)}</span>{' '}
                {f.from} → {f.to}: {KIND_LABEL[f.kind]} {gbp(f.grossMinor)}
              </button>
              <span className="state">{statusLabel(f)}</span>
              <Mode mode={f.mode} livemode={f.livemode} />
              <span className="hint"> {f.providerObjectId ? redact(f.providerObjectId) : 'no provider ID yet'}</span>
              <span className="hint"> gross {gbp(f.grossMinor)}, retention {gbp(f.retentionMinor)}, fees {gbp(f.feeMinor)}, net {gbp(netMinor(f))}, settled {gbp(f.settledMinor)}</span>
            </li>
          )
        })}
      </ul>
      {selected ? <Inspector f={selected} /> : <p className="hint">Select a flow to see why it was paid or held.</p>}
      <h3>Saga evidence</h3>
      <ol className="saga">
        {d.saga.map((s) => (
          <li key={s.stepId}>
            {s.name} - {s.status} <Mode mode={s.mode} livemode={false} />{' '}
            <span className="hint">{s.providerObjectId ? redact(s.providerObjectId) : 'no provider object'}{s.evidenceIds.length ? `; evidence ${s.evidenceIds.join(', ')}` : ''}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}

export default MoneyView
