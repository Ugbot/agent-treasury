import { ModeChip } from '../components/ModeChip'
import type { RunResult, ScenarioInfo } from '../scenarios/types'
import type { ScenarioRunner } from './useScenarioRunner'

interface Plain { name: string; blurb: string }
const PLAIN: Record<string, Plain> = {
  'deliver_and_release': { name: 'Deliver and release', blurb: 'Delivery is proven, so the merchant and driver are paid. The merchant leg needs approval.' },
  'fuel_entitlement': { name: 'Fuel allowance', blurb: 'The driver gets a capped fuel allowance, revoked if coverage lapses.' },
  'coverage_replan': { name: 'Replan on coverage change', blurb: 'Coverage changes mid-job, so the agent replans and policy re-checks the new plan.' },
  'cancel_refund': { name: 'Cancel and refund', blurb: 'Cancelling before pickup refunds the payment. After pickup it escalates to a person.' },
  'delivery-pay': { name: 'Deliver and release', blurb: 'Delivery is confirmed, so the merchant and driver are paid.' },
  'happy-path': { name: 'Deliver and release', blurb: 'Delivery is confirmed, so the merchant and driver are paid.' },
  'delivery-happy': { name: 'Deliver and release', blurb: 'Delivery is confirmed, so the merchant and driver are paid.' },
  'fuel-card': { name: 'Fuel allowance', blurb: 'The driver gets a capped fuel card for this trip.' },
  'coverage-replan': { name: 'Replan on coverage change', blurb: 'Insurance coverage changes, so the agent picks a new route and policy re-checks it.' },
  'cancel-refund': { name: 'Cancel and refund', blurb: 'The sender cancels before pickup and the payment is refunded.' },
  'prohibited-zone': { name: 'Route enters a prohibited zone', blurb: 'The route crosses a forbidden area, so policy denies payment.' },
  'over-limit': { name: 'Amount above the approval limit', blurb: 'The payment is large enough that a person must approve it.' },
}
export const plainScenario = (s: ScenarioInfo): Plain => PLAIN[s.id] ?? { name: s.title.replace(/^\d+\.\s*/, ''), blurb: '' }

/** One-line fixes for the capabilities the backend can report as unavailable. */
export function fixFor(unavailable: string): string {
  const u = unavailable.toLowerCase()
  if (u.includes('issuing')) return 'Enable Issuing in the Stripe test dashboard, then run it again.'
  if (u.includes('key') || u.includes('stripe')) return 'Add a Stripe test key to the backend settings, or switch the payment mode to Mock.'
  if (u.includes('agent') || u.includes('llm') || u.includes('model')) return 'Configure the agent model in the backend settings, then ask the agent again.'
  return 'Fix the setting above, or switch the payment mode to Mock.'
}

export function RunResultNote({ r }: { r: RunResult }) {
  return (
    <div className="run-result" aria-live="polite">
      <p className="run-head"><strong>{r.scenarioId}</strong> <span className="badge">{r.status}</span> <ModeChip mode={r.mode} /></p>
      {r.unavailable && (
        <p className="unavailable" role="note"><strong>Unavailable:</strong> {r.unavailable}<br /><span className="fix">Fix: {fixFor(r.unavailable)}</span></p>
      )}
      <ol className="run-steps">{r.steps.map((s, j) => <li key={j}><span>{s.name}</span> <span className="hint">{s.status}{s.detail ? `: ${s.detail}` : ''}</span></li>)}</ol>
    </div>
  )
}

const MAIN_IDS = new Set(['deliver_and_release', 'fuel_entitlement', 'coverage_replan', 'cancel_refund', 'delivery-pay', 'fuel-card', 'coverage-replan', 'cancel-refund'])

function ScenarioItem({ s, r }: { s: ScenarioInfo; r: ScenarioRunner }) {
  const p = plainScenario(s)
  const running = r.busy === `run:${s.id}`
  return (
    <li>
      <div>
        <h3>{p.name}</h3>
        {p.blurb && <p>{p.blurb}</p>}
      </div>
      <button type="button" onClick={() => void r.run(s)} disabled={r.busy !== null} aria-label={`Run ${p.name}`}>{running ? 'Running...' : 'Run'}</button>
    </li>
  )
}

export function ScenarioRail({ r }: { r: ScenarioRunner }) {
  const main = r.list.filter((s) => MAIN_IDS.has(s.id))
  const extra = r.list.filter((s) => !MAIN_IDS.has(s.id))
  if (main.length === 0) main.push(...extra.splice(0))
  return (
    <section className="rail" aria-labelledby="scn-h" aria-busy={r.busy !== null}>
      <h2 id="scn-h">Scenarios</h2>
      <fieldset className="mode-pick">
        <legend>Payment mode</legend>
        <label className={r.mode === 'mock' ? 'on' : ''}>
          <input type="radio" name="run-mode" value="mock" checked={r.mode === 'mock'} onChange={() => r.setMode('mock')} disabled={r.busy !== null} />
          <span>Mock<small>Simulated, Stripe is not called</small></span>
        </label>
        <label className={r.mode === 'stripe_test' ? 'on' : ''}>
          <input type="radio" name="run-mode" value="stripe_test" checked={r.mode === 'stripe_test'} onChange={() => r.setMode('stripe_test')} disabled={r.busy !== null} />
          <span>Stripe test<small>Creates Stripe test objects, no real money</small></span>
        </label>
      </fieldset>
      {r.error && <div role="alert" className="error inline">{r.error}</div>}
      <ul className="scn">
        {main.map((s) => <ScenarioItem key={s.id} s={s} r={r} />)}
        {r.list.length === 0 && !r.error && <li className="empty">No scenarios reported. Check that the backend is running and open /api/scenarios.</li>}
      </ul>
      {extra.length > 0 && (
        <details className="more">
          <summary>Smaller test cases ({extra.length})</summary>
          <ul className="scn">{extra.map((s) => <ScenarioItem key={s.id} s={s} r={r} />)}</ul>
        </details>
      )}
      {r.lastRun && <RunResultNote r={r.lastRun} />}
    </section>
  )
}
