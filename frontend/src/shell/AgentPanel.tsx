import { StepCard } from '../scenarios'
import type { ScenarioRunner } from './useScenarioRunner'

/** What the agent proposed next to what policy allowed. The agent has no payment authority. */
export function AgentPanel({ r, onAudit }: { r: ScenarioRunner; onAudit?: (seq: number) => void }) {
  return (
    <section className="agent-panel" aria-labelledby="agent-h">
      <div className="agent-head">
        <div>
          <h2 id="agent-h">Agent proposal and policy verdict</h2>
          <p className="hint">The agent suggests a payment. Policy decides whether it happens. The agent cannot move money itself.</p>
        </div>
        <button type="button" onClick={() => void r.step()} disabled={r.busy !== null}>{r.busy === 'step' ? 'Asking the agent...' : 'Ask the agent for the next payment'}</button>
      </div>
      {r.steps.length === 0
        ? <p className="empty">No proposal yet. Ask the agent to see what it proposes and what policy allows.</p>
        : r.steps.map((s, i) => <StepCard key={r.steps.length - i} step={s} n={r.steps.length - i} onAudit={onAudit} />)}
    </section>
  )
}
