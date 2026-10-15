import { useCallback, useMemo, useState } from 'react'
import { clientFromEnv } from './api'
import type { TreasuryClient } from './api'
import { JourneyView, journeyClientFromEnv } from './journey'
import { AuditLogView, auditClientFromEnv, createAuditFixtureClient } from './audit'
import type { AuditClient } from './audit'
import { createHttpChatTransport, unavailableTransport } from './chat'
import { ModeBadge } from './components/ModeBadge'
import { PoliciesView, createPoliciesFixtureClient } from './policies'
import { AuthorView } from './author'
import { PaymentHistory, createHistoryFixtureClient, createHistoryHttpClient } from './history'
import { createScenariosFixtureClient, createScenariosHttpClient } from './scenarios'
import type { ScenariosClient } from './scenarios'
import { MissionControl } from './shell/MissionControl'
import { WhyDrawer, createWhyFixtureClient, createWhyHttpClient } from './why'
import type { WhyClient } from './why'
import { FreezeControl, useFreeze, freezeClientFromEnv } from './freeze'
import { useTreasury } from './useTreasury'
import { DemoMode } from './demo'

type View = 'mission' | 'log' | 'policies' | 'history' | 'journey' | 'author'
const VIEWS: [View, string][] = [['mission', 'Mission control'], ['log', 'Everything log'], ['policies', 'Policies'], ['author', 'Write policy'], ['history', 'History']]

export default function App({ client, audit, why, scenarios }: { client?: TreasuryClient; audit?: AuditClient; why?: WhyClient; scenarios?: ScenariosClient }) {
  const c = useMemo(() => client ?? clientFromEnv(), [client])
  const t = useTreasury(c)
  const pc = useMemo(() => (c.kind === 'fixture' ? createPoliciesFixtureClient() : undefined), [c])
  const s = t.snapshot
  const [view, setView] = useState<View>('mission')
  const fixture = c.kind === 'fixture'
  const auditClient = useMemo(() => audit ?? (fixture ? createAuditFixtureClient() : auditClientFromEnv('http')), [audit, fixture])
  const whyClient = useMemo(() => why ?? (fixture ? createWhyFixtureClient() : createWhyHttpClient()), [why, fixture])
  const scnClient = useMemo(() => scenarios ?? (fixture ? createScenariosFixtureClient() : createScenariosHttpClient()), [scenarios, fixture])
  const histClient = useMemo(() => (fixture ? createHistoryFixtureClient() : createHistoryHttpClient()), [fixture])
  const freezeClient = useMemo(() => freezeClientFromEnv(fixture), [fixture])
  const fz = useFreeze(freezeClient)
  const jClient = useMemo(() => journeyClientFromEnv(), [])
  const [histId, setHistId] = useState<string | null>(null)
  const openHistory = useCallback((id: string) => { setHistId(id); setWhyId(null); setView('history') }, [])
  const [whyId, setWhyId] = useState<string | null>(null)
  const [hlSeq, setHlSeq] = useState<number | null>(null)
  const openAudit = useCallback((seq: number) => { setHlSeq(seq); setWhyId(null); setView('log') }, [])
  const closeWhy = useCallback(() => setWhyId(null), [])
  const chatTransport = useMemo(() => (c.kind === 'fixture' ? unavailableTransport : createHttpChatTransport()), [c])
  const demoHost = useMemo(() => ({
    setView: (v: 'policies' | 'mission') => setView(v),
    openWhy: (id: string) => setWhyId(id),
    closeWhy: () => setWhyId(null),
    fallbackPaymentId: () => s?.operation?.operationId ?? null,
  }), [s?.operation?.operationId])
  const onRecord = useCallback((on: boolean) => document.body.classList.toggle('demo-recording', on), [])
  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <header className="top">
        <div className="brand">
          <h1>Agent Treasury</h1>
        </div>
        <nav aria-label="Views" className="views" ref={(el) => { if (el) el.scrollLeft = 0 }}>
          <button type="button" aria-pressed={view === 'journey'} onClick={() => setView('journey')}>New job</button>
          {VIEWS.map(([id, label]) => (
            <button key={id} type="button" aria-pressed={view === id} onClick={() => setView(id)}>{label}</button>
          ))}
        </nav>
        <div className="top-meta">
          {s && <span className="mission-id">Mission {s.missionId}</span>}
          {s && <ModeBadge mode={s.mode} />}
          {fixture && <span className="banner" role="note">Fixture mode: canned data, no backend</span>}
          <DemoMode host={demoHost} fixture={fixture} onRecord={onRecord} />
          <FreezeControl state={fz.state} error={fz.error} onSet={fz.set} />
          <button type="button" className="quiet" onClick={() => void t.refresh()} disabled={t.loading || t.pending}>Refresh state</button>
        </div>
      </header>
      {t.error && <div role="alert" className="error">{t.error}</div>}
      {view === 'policies' && <div className="page"><PoliciesView client={pc} missionId={s?.missionId} /></div>}
      {view === 'author' && <div className="page"><AuthorView onOpenPolicies={() => setView('policies')} /></div>}
      {view === 'log' && (
        <div className="page">
          <AuditLogView client={auditClient} highlightSeq={hlSeq} onWhy={(id) => setWhyId(id)} />
        </div>
      )}
      {view === 'journey' && <div className="page"><JourneyView client={jClient} onExit={() => setView('mission')} /></div>}
      {view === 'history' && <PaymentHistory client={histClient} paymentId={histId ?? s?.missionId ?? ''} />}
      {whyId && <WhyDrawer client={whyClient} paymentId={whyId} onClose={closeWhy} onOpenAudit={openAudit} onOpenHistory={openHistory} />}
      <div hidden={view !== 'mission'}>
        <MissionControl freeze={fz.state} t={t} scenarios={scnClient} audit={auditClient} chat={chatTransport} onWhy={setWhyId} onAudit={openAudit} />
      </div>
    </>
  )
}
