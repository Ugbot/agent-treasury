import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createDemoFixtureClient, createDemoHttpClient } from './client'
import type { DemoClient } from './client'
import { DEMO_STEPS, freshRunId, planSteps, stepAt, stepStart, totalSeconds } from './script'
import './demo.css'

export interface DemoHost {
  setView: (v: 'policies' | 'mission') => void
  openWhy: (paymentId: string) => void
  closeWhy: () => void
  /** Payment id for the Why drawer: the last demo run's id, else the current operation. */
  fallbackPaymentId: () => string | null
}

const reduced = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export function DemoMode({ host, client, fixture, onRecord, tickMs = 250 }: {
  host: DemoHost
  client?: DemoClient
  fixture?: boolean
  onRecord?: (on: boolean) => void
  tickMs?: number
}) {
  const c = useMemo(() => client ?? (fixture ? createDemoFixtureClient() : createDemoHttpClient()), [client, fixture])
  const [open, setOpen] = useState(false)
  const [steps, setSteps] = useState(DEMO_STEPS)
  const [elapsed, setElapsed] = useState(0)
  const [paused, setPaused] = useState(false)
  const [record, setRecord] = useState(false)
  const [note, setNote] = useState('')
  const [done, setDone] = useState(false)
  const ran = useRef(new Set<string>())
  const lastId = useRef<string | null>(null)
  const runId = useRef('')

  const enter = useCallback(async (i: number, list = steps) => {
    const s = list[i]
    if (!s || ran.current.has(`${runId.current}:${s.id}`)) return
    ran.current.add(`${runId.current}:${s.id}`)
    const a = s.action
    if (a.kind === 'view') { host.closeWhy(); host.setView(a.view); setNote('') }
    else if (a.kind === 'run') {
      host.closeWhy(); host.setView('mission'); setNote('Running...')
      const r = await c.run(a.scenario, `${runId.current}-${s.id}`)
      if (r.paymentId && a.scenario === 'deliver_and_release') lastId.current = r.paymentId
      setNote(r.ok ? `Mode: ${r.note}` : `Could not run: ${r.note}`)
    } else if (a.kind === 'why') {
      const id = lastId.current ?? host.fallbackPaymentId()
      if (id) host.openWhy(id)
      setNote(id ? '' : 'No payment to explain yet.')
    } else setNote('')
  }, [c, host, steps])

  const start = useCallback(async () => {
    runId.current = freshRunId()
    ran.current = new Set()
    lastId.current = null
    const list = planSteps(await c.freezeAvailable())
    setSteps(list)
    setElapsed(0); setDone(false); setPaused(false); setOpen(true)
    void enter(0, list)
  }, [c, enter])

  const cur = open && !done ? stepAt(steps, elapsed) : null
  const total = totalSeconds(steps)

  useEffect(() => {
    if (!open || paused || done) return
    const id = window.setInterval(() => setElapsed((e) => e + tickMs / 1000), tickMs)
    return () => window.clearInterval(id)
  }, [open, paused, done, tickMs])

  useEffect(() => {
    if (!open || done) return
    if (!cur) { setDone(true); host.closeWhy(); return }
    void enter(cur.index)
  }, [cur?.index, open, done]) // eslint-disable-line react-hooks/exhaustive-deps

  const skip = () => { if (cur) setElapsed(stepStart(steps, cur.index) + steps[cur.index].seconds + 0.001) }
  const stop = useCallback(() => { setOpen(false); setDone(false); setRecord(false); onRecord?.(false); host.closeWhy() }, [host, onRecord])
  const toggleRecord = () => { setRecord((r) => { onRecord?.(!r); return !r }) }

  useEffect(() => {
    if (!open) return
    const k = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (e.code === 'Space' && !(el && /INPUT|TEXTAREA|SELECT|BUTTON/.test(el.tagName))) { e.preventDefault(); setPaused((p) => !p) }
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [open])

  if (!open) return <button type="button" className="quiet demo-entry" onClick={() => void start()}>Demo mode</button>

  const pct = Math.min(100, (elapsed / total) * 100)
  const mm = (n: number) => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, '0')}`
  return (
    <div className={`demo ${record ? 'demo-record' : ''} ${reduced() ? 'demo-still' : ''}`} role="region" aria-label="Guided demo">
      <div className="demo-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}><span style={{ width: `${pct}%` }} /></div>
      <p className="demo-caption" aria-live="polite">{done ? 'That is the whole loop: policy, payment, proof.' : steps[cur?.index ?? 0].caption}</p>
      {note && <p className="demo-note" role="status">{note}</p>}
      <div className="demo-controls">
        <span className="demo-timer">{mm(elapsed)} / {mm(total)}</span>
        {!done && <span className="demo-step">Step {(cur?.index ?? 0) + 1} of {steps.length}</span>}
        <button type="button" onClick={() => setPaused((p) => !p)} disabled={done}>{paused ? 'Resume' : 'Pause'}</button>
        <button type="button" onClick={() => void start()}>Restart</button>
        <button type="button" onClick={skip} disabled={done}>Skip</button>
        <button type="button" aria-pressed={record} onClick={toggleRecord}>Record</button>
        <button type="button" onClick={stop}>Exit demo</button>
      </div>
    </div>
  )
}
