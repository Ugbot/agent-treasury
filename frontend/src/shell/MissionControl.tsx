import { useState } from 'react'
import type { AuditClient } from '../audit'
import { ChatSidebar } from '../chat'
import type { ChatTransport } from '../chat'
import { TriggerDeck } from '../components/TriggerDeck'
import { MapView } from '../map'
import { MoneyView } from '../money/MoneyView'
import { PARTIES, PartyView } from '../parties'
import type { ScenariosClient } from '../scenarios'
import type { TreasuryState } from '../useTreasury'
import { ActivityFeed } from './ActivityFeed'
import { AgentPanel } from './AgentPanel'
import { DecisionPipeline } from './DecisionPipeline'
import { ScenarioRail } from './ScenarioRail'
import { StatusHeadline } from './StatusHeadline'
import { FreezeBanner } from '../freeze/FreezeControl'
import type { FreezeState } from '../freeze/types'
import { useScenarioRunner } from './useScenarioRunner'

type Lens = 'pipeline' | 'sender' | 'receiver' | 'driver' | 'all'
const LENSES: [Lens, string][] = [['pipeline', 'Decision pipeline'], ['sender', 'Sender'], ['receiver', 'Receiver'], ['driver', 'Driver'], ['all', 'All three side by side']]

export function MissionControl({ t, scenarios, audit, chat, onWhy, onAudit, freeze }: {
  freeze?: FreezeState | null
  t: TreasuryState
  scenarios: ScenariosClient
  audit: AuditClient
  chat: ChatTransport
  onWhy: (paymentId: string) => void
  onAudit: (seq: number) => void
}) {
  const s = t.snapshot
  const [lens, setLens] = useState<Lens>('pipeline')
  const runner = useScenarioRunner(scenarios, s?.missionId, () => void t.refresh())

  if (t.loading && !s) return <main id="main" className="mc-empty"><p role="status">Loading the current mission...</p></main>
  if (!s) {
    return (
      <main id="main" className="mc-empty">
        <p>Backend unavailable. Start the API on port 8000, or run the frontend with VITE_API_MODE=fixture to use canned data.</p>
        <button type="button" onClick={() => void t.refresh()}>Try again</button>
      </main>
    )
  }
  const op = s.operation
  return (
    <main id="main" className="mc">
      <FreezeBanner state={freeze ?? null} />
      <StatusHeadline
        s={s}
        freeze={freeze}
        action={
          <div className="why-cta">
            <button type="button" className="primary" disabled={!op} onClick={() => op && onWhy(op.operationId)}>Why was this paid?</button>
            {!op && <span className="hint">Available once a payment is made.</span>}
          </div>
        }
      />
      <div className="mc-grid">
        <aside className="mc-left"><ScenarioRail r={runner} /></aside>
        <div className="mc-centre">
          <div className="lens" role="group" aria-label="What to show">
            {LENSES.map(([id, label]) => (
              <button key={id} type="button" aria-pressed={lens === id} onClick={() => setLens(id)}>{label}</button>
            ))}
          </div>
          {lens === 'pipeline' ? (
            <>
              <DecisionPipeline s={s} />
              <AgentPanel r={runner} onAudit={onAudit} />
              <details className="more" open>
                <summary>Change the inputs yourself</summary>
                <TriggerDeck t={t} />
              </details>
              <details className="more">
                <summary>Simulated route and zones</summary>
                <p className="hint sim-note">Driver positions, zones and routes are simulated for this demo.</p>
                <MapView mode={s.mode} />
              </details>
              <details className="more">
                <summary>Funds and allowances</summary>
                <MoneyView />
              </details>
              <details className="more">
                <summary>Ask about this decision</summary>
                <ChatSidebar missionId={s.missionId} decision={s.decision} transport={chat} />
              </details>
            </>
          ) : (
            <div className={`party-grid ${lens === 'all' ? 'side-by-side' : ''}`}>
              {(lens === 'all' ? PARTIES : [lens]).map((p) => <PartyView key={p} party={p} t={t} audit={audit} />)}
            </div>
          )}
        </div>
        <aside className="mc-right"><ActivityFeed audit={audit} onOpen={onAudit} /></aside>
      </div>
    </main>
  )
}
