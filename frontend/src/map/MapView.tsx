import { useEffect, useRef, useState } from 'react'
import { FIXTURE_CLAUSES, FIXTURE_MAP, FIXTURE_PLAN } from './fixture'
import { fmt, inPolygon, pointAt, pts } from './geometry'
import type { DataMode, MapClause, MapPlan, MapState, PositionEvent } from './types'

export interface MapViewProps {
  map?: MapState
  clauses?: MapClause[]
  plan?: MapPlan | null
  mode?: DataMode
  /** Receives position events only. There is deliberately no payment callback. */
  onPositionEvent?: (e: PositionEvent) => void
  tickMs?: number
  steps?: number
}

export function MapView({ map = FIXTURE_MAP, clauses = FIXTURE_CLAUSES, plan = FIXTURE_PLAN, mode = 'fixture', onPositionEvent, tickMs = 500, steps = 20 }: MapViewProps) {
  const driver = map.drivers[0]
  const active = map.routes.find((r) => r.status === 'active') ?? map.routes.find((r) => r.status === 'proposed')
  const old = map.routes.filter((r) => r.status === 'replaced' || r.status === 'denied')
  const path = active?.points ?? (driver ? [driver.position] : [])
  const [step, setStep] = useState(0)
  const [running, setRunning] = useState(false)
  const [replay, setReplay] = useState(false)
  const maxStep = useRef(0)
  const emit = useRef(onPositionEvent)
  useEffect(() => { emit.current = onPositionEvent })

  const pos = pointAt(path, step / steps)

  function emitAt(s: number) {
    if (!driver) return
    const p = pointAt(path, s / steps)
    emit.current?.({ type: 'position', driverId: driver.driverId, x: p.x, y: p.y, assignmentVersion: driver.assignmentVersion, simTime: s, source: 'simulation' })
  }
  function advance(to: number) {
    const s = Math.min(steps, to)
    setStep(s); maxStep.current = Math.max(maxStep.current, s); setReplay(false); emitAt(s)
    return s
  }
  useEffect(() => {
    if (!running) return
    const id = setInterval(() => {
      setStep((cur) => {
        const s = Math.min(steps, cur + 1)
        maxStep.current = Math.max(maxStep.current, s)
        emitAt(s)
        if (s >= steps) setRunning(false)
        return s
      })
    }, tickMs)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, tickMs, steps])

  const failed = clauses.filter((c) => !c.passed)
  const inZone = map.zones.find((z) => inPolygon(pos, z.polygon))
  const label = mode === 'stripe_test' ? 'SIMULATED coordinates (Stripe test mode payments are separate)' : `SIMULATED coordinates (${mode})`
  const fillFor = (permitted: boolean) => (permitted ? 'var(--map-ok, #cfe8d5)' : 'var(--map-no, #f3c9c9)')

  return (
    <section aria-labelledby="map-h" className="panel map-view">
      <h2 id="map-h">Travel map <span className="badge" data-testid="sim-label">SIMULATED</span></h2>
      <p className="hint">{label}. Controls emit position events only; they cannot authorise or move money.</p>
      <svg viewBox="0 0 100 100" role="img" aria-labelledby="map-title" aria-describedby="map-desc" style={{ width: '100%', maxWidth: 560, background: 'var(--map-bg, #f6f6f2)', border: '1px solid #999' }}>
        <title id="map-title">Simulated delivery map</title>
        <desc id="map-desc">
          {`Simulated. Driver ${driver?.name ?? 'none'} at ${fmt(pos)}. ${map.zones.length} zones, ${map.fuelStops.length} fuel stops. ${inZone ? `Inside ${inZone.label}.` : 'Outside all zones.'}`}
        </desc>
        {map.zones.map((z) => (
          <polygon key={z.zoneId} points={pts(z.polygon)} fill={fillFor(z.permitted)} fillOpacity={0.6} stroke={z.permitted ? '#2a7a3b' : '#b02a2a'} strokeDasharray={z.permitted ? undefined : '2 1'} data-testid={`zone-${z.zoneId}`}>
            <title>{`${z.label} (${z.permitted ? 'permitted' : 'prohibited'})`}</title>
          </polygon>
        ))}
        {old.map((r) => <polyline key={r.routeId} points={pts(r.points)} fill="none" stroke="#888" strokeWidth={0.8} strokeDasharray="2 2" data-testid={`route-${r.routeId}`} />)}
        {active && <polyline points={pts(active.points)} fill="none" stroke="#1b5fb5" strokeWidth={1} data-testid={`route-${active.routeId}`} />}
        {map.fuelStops.map((f) => (
          <g key={f.placeId} data-testid={`fuel-${f.placeId}`}>
            <rect x={f.position.x - 1.5} y={f.position.y - 1.5} width={3} height={3} fill="#e0a800" stroke="#333" strokeWidth={0.3} />
            <text x={f.position.x + 2.5} y={f.position.y + 1} fontSize={2.5}>{f.label}</text>
          </g>
        ))}
        {[map.merchant, map.destination].map((p) => (
          <g key={p.placeId} data-testid={`place-${p.placeId}`}>
            <circle cx={p.position.x} cy={p.position.y} r={2} fill="#333" />
            <text x={p.position.x + 3} y={p.position.y - 2} fontSize={3}>{p.label}</text>
          </g>
        ))}
        {map.drivers.map((d, i) => {
          const p = i === 0 ? pos : d.position
          return (
            <g key={d.driverId} data-testid={`driver-${d.driverId}`} opacity={replay && i === 0 ? 0.6 : 1}>
              <circle cx={p.x} cy={p.y} r={2} fill={d.eligible ? '#2a7a3b' : '#b02a2a'} stroke="#fff" strokeWidth={0.5} />
              <text x={p.x + 3} y={p.y + 4} fontSize={2.5}>{d.name}{d.eligible ? '' : ' (ineligible)'}</text>
            </g>
          )
        })}
        <text x={1} y={98} fontSize={3} fill="#555">SIMULATED</text>
      </svg>

      <div role="group" aria-label="Playback controls" className="row">
        <button type="button" onClick={() => { if (!running && step >= steps) setStep(0); setRunning(!running) }}>{running ? 'Pause' : 'Run'}</button>
        <button type="button" disabled={running || step >= steps} onClick={() => advance(step + 1)}>Step</button>
        <label>Scrub (history view only)
          <input type="range" min={0} max={maxStep.current} value={step} disabled={running}
            aria-label="Scrub history"
            onChange={(e) => { setStep(Number(e.target.value)); setReplay(Number(e.target.value) < maxStep.current) }} />
        </label>
        <span role="status">{replay ? 'Replaying history: no events sent' : `Step ${step}/${steps}`}</span>
      </div>

      <ul aria-label="Legend" className="plain">
        <li><svg width="12" height="12" aria-hidden="true"><rect width="12" height="12" fill="#cfe8d5" stroke="#2a7a3b" /></svg> Permitted zone / geofence</li>
        <li><svg width="12" height="12" aria-hidden="true"><rect width="12" height="12" fill="#f3c9c9" stroke="#b02a2a" strokeDasharray="2 1" /></svg> Prohibited zone</li>
        <li><svg width="12" height="12" aria-hidden="true"><rect x="2" y="2" width="8" height="8" fill="#e0a800" /></svg> Fuel stop</li>
        <li>Solid blue line: current route. Dashed grey: replaced or denied route</li>
        <li>Green driver: eligible and covered. Red driver: ineligible</li>
      </ul>

      <h3>Policy overlay</h3>
      <p>{inZone ? `Driver is inside ${inZone.label} (${inZone.permitted ? 'permitted' : 'prohibited'}).` : 'Driver is outside all zones.'} Coverage: {driver ? `${driver.eligible ? 'eligible' : 'ineligible'} (record ${driver.coverageRecordId}, assignment v${driver.assignmentVersion})` : 'no driver'}</p>
      {failed.length > 0 ? (
        <ul aria-label="Failed clauses">{failed.map((c) => <li key={c.clauseId}>{c.prose}: observed {c.observed ?? 'n/a'}, expected {c.expected ?? 'n/a'}</li>)}</ul>
      ) : <p>No failed clauses.</p>}
      {plan && (
        <div aria-label="Replacement plan">
          <h4>Replacement plan {plan.planId}{plan.replaces ? ` (replaces ${plan.replaces})` : ''}</h4>
          <p>{plan.reason}</p>
          <ol>{plan.actions.map((a) => <li key={a}>{a}</li>)}</ol>
          <p className="hint">{mode === 'stripe_test' ? 'Backend plan. Provider effects appear in the event feed.' : 'Not a Stripe transaction.'}</p>
        </div>
      )}
    </section>
  )
}
