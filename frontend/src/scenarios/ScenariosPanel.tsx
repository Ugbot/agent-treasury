import { useEffect, useState } from 'react'
import { ModeChip, VerdictChip } from '../components/ModeChip'
import { errMsg, formatMoney } from '../lib/wire'
import type { AgentStep, RunMode, RunResult, ScenarioInfo, ScenariosClient } from './types'

export function StepCard({ step, n, onAudit }: { step: AgentStep; n: number; onAudit?: (seq: number) => void }) {
  if (!step.available) {
    return (
      <article className="step unavailable" aria-label={`Agent step ${n}`}>
        <h4>Step {n}: agent unavailable</h4>
        <p>{step.unavailable}</p>
      </article>
    )
  }
  const p = step.proposal
  const v = step.verdict
  return (
    <article className="step" aria-label={`Agent step ${n}`}>
      <h4>Step {n} <ModeChip mode={step.mode} />{step.auditSeq != null && (
        <> <button type="button" className="link" onClick={() => onAudit?.(step.auditSeq!)}>audit #{step.auditSeq}</button></>)}
      </h4>
      <div className="sbs">
        <section aria-label="Agent proposal" className="side">
          <h5>Agent proposes{p ? ` (${p.agent})` : ''}</h5>
          {p ? (
            <>
              <p className="act">{p.action}</p>
              {p.summary && <p>{p.summary}</p>}
              <dl>
                {p.amountMinor != null && (<><dt>amount</dt><dd>{p.currency ? formatMoney(p.amountMinor, p.currency) : `${p.amountMinor} minor units`}</dd></>)}
                {p.recipient && (<><dt>to</dt><dd>{p.recipient}</dd></>)}
              </dl>
              {p.rationale && <p className="hint">{p.rationale}</p>}
            </>
          ) : <p>No proposal returned.</p>}
        </section>
        <section aria-label="Policy verdict" className={`side verdict-side ${v?.verdict ? `vs-${v.verdict}` : ''}`}>
          <h5>Policy verdict</h5>
          {v ? (
            <>
              <p className={`verdict ${v.verdict ? `v-${v.verdict}` : ''}`}>{v.verdict ?? 'no verdict'}</p>
              {v.executed != null && <p className="hint">{v.executed ? 'Executed through the saga.' : 'Not executed.'}</p>}
              <ul className="plain">
                {v.clauses.map((c, i) => <li key={i}><strong>{c.clauseId}</strong> <VerdictChip verdict={c.outcome} />{c.detail ? <span className="hint"> {c.detail}</span> : null}</li>)}
                {v.reasons.map((r, i) => <li key={`r${i}`}>{r}</li>)}
              </ul>
            </>
          ) : <p>No verdict returned.</p>}
        </section>
      </div>
    </article>
  )
}

export function ScenariosPanel({ client, missionId, onRan, onAudit }: {
  client: ScenariosClient
  missionId?: string | null
  onRan?: (r: RunResult) => void
  onAudit?: (seq: number) => void
}) {
  const [list, setList] = useState<ScenarioInfo[]>([])
  const [mode, setMode] = useState<RunMode>('mock')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [runs, setRuns] = useState<RunResult[]>([])
  const [steps, setSteps] = useState<AgentStep[]>([])
  const [mission, setMission] = useState<string | null>(missionId ?? null)

  useEffect(() => {
    let live = true
    client.list().then((l) => live && setList(l), (e) => live && setError(`Could not load scenarios: ${errMsg(e)}`))
    return () => { live = false }
  }, [client])
  useEffect(() => { if (missionId) setMission(missionId) }, [missionId])

  async function act<T>(key: string, fn: () => Promise<T>, done: (t: T) => void) {
    setBusy(key)
    setError(null)
    try {
      done(await fn())
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setBusy(null)
    }
  }
  const run = (s: ScenarioInfo) =>
    act(`run:${s.id}`, () => client.run(s.id, mode), (r) => {
      setRuns((c) => [r, ...c])
      if (r.missionId) setMission(r.missionId)
      setSteps([])
      onRan?.(r)
    })
  const step = () => act('step', () => client.step(mission ?? 'current'), (s) => setSteps((c) => [s, ...c]))

  return (
    <section className="panel scenarios" aria-labelledby="scn-h" aria-busy={busy !== null}>
      <h2 id="scn-h">Scenarios and agent</h2>
      <p className="hint">One click runs a scenario on the backend. The agent proposes; policy decides. The agent has no payment authority.</p>
      {error && <div role="alert" className="error inline">{error}</div>}
      <div>
        <label htmlFor="run-mode">Payment mode</label>
        <select id="run-mode" value={mode} onChange={(e) => setMode(e.target.value as RunMode)} disabled={busy !== null}>
          <option value="mock">mock (simulated, no Stripe)</option>
          <option value="stripe_test">stripe_test (Stripe test mode)</option>
        </select>
      </div>
      <ul className="scn-list">
        {list.map((s) => (
          <li key={s.id}>
            <span>{s.title}</span>
            <button type="button" onClick={() => void run(s)} disabled={busy !== null}>{busy === `run:${s.id}` ? 'Running...' : 'Run'}</button>
          </li>
        ))}
        {list.length === 0 && !error && <li>No scenarios reported by the backend.</li>}
      </ul>

      {runs.length > 0 && (
        <div aria-live="polite">
          <h3>Runs</h3>
          {runs.map((r, i) => (
            <article key={i} className="run">
              <p><strong>{r.scenarioId}</strong> <span className="badge">{r.status}</span> <ModeChip mode={r.mode} />{r.missionId && <span className="hint"> mission {r.missionId}</span>}</p>
              {r.unavailable && <p className="note-unavailable" role="note">Unavailable: {r.unavailable}</p>}
              <ol className="plain">{r.steps.map((s, j) => <li key={j}>{s.name}: {s.status}{s.detail ? ` (${s.detail})` : ''}</li>)}</ol>
            </article>
          ))}
        </div>
      )}

      <div className="agent">
        <h3>Agent</h3>
        <button type="button" onClick={() => void step()} disabled={busy !== null}>{busy === 'step' ? 'Asking agent...' : 'Step agent'}</button>
        <span className="hint"> mission {mission ?? 'current'}</span>
        {steps.map((s, i) => <StepCard key={steps.length - i} step={s} n={steps.length - i} onAudit={onAudit} />)}
      </div>
    </section>
  )
}
