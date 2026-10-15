import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { journeyClientFromEnv } from './client'
import './journey.css'
import type { Effective, Job, JobDraft, JourneyClient, LibraryPolicy, PreviewResult, Watch } from './types'
import { JourneyApiError, money, toMinor } from './types'

const STEPS = ['Define job', 'Attach policies', 'Preview', 'Dispatch & watch'] as const

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e))

function ModeBadges({ mode, simulated = true }: { mode: string; simulated?: boolean }) {
  return (
    <>
      <span className={`badge mode-${mode}`}>{mode === 'stripe_test' ? 'STRIPE TEST' : 'MOCK'}</span>
      {simulated && <span className="badge source-simulation">SIMULATED</span>}
    </>
  )
}

function ruleText(r: Effective['rules'][number]) {
  return r.clauses.map((c) => ({ id: c.id, prose: c.prose }))
}

function EffectivePanel({ eff }: { eff: Effective | null }) {
  return (
    <aside className="jpanel" aria-labelledby="eff-h">
      <h3 id="eff-h">Effective rules</h3>
      <p className="hint">Organisation rules always apply and cannot be removed. The strictest rule wins.</p>
      {!eff ? <p className="empty">Rules appear once the job is saved.</p> : (
        <ul className="jrules" data-testid="effective-rules">
          {eff.rules.map((r) => (
            <li key={r.attachment_id} className={r.scope === 'organisation' ? 'org' : ''}>
              <strong>{r.title}</strong> v{r.version}{' '}
              <span className="badge party">{r.scope === 'organisation' ? 'ORG, LOCKED' : r.locked ? 'LOCKED' : 'JOB'}</span>
              <ul className="plain">{ruleText(r).map((c) => <li key={c.id}><code>{c.id}</code> {c.prose}</li>)}</ul>
            </li>
          ))}
        </ul>
      )}
    </aside>
  )
}

export interface JourneyViewProps {
  client?: JourneyClient
  /** Watch poll interval; the product default is one second. */
  pollMs?: number
  onExit?: () => void
}

export function JourneyView({ client, pollMs = 1000, onExit }: JourneyViewProps) {
  const c = useMemo(() => client ?? journeyClientFromEnv(), [client])
  const [step, setStep] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const [eff, setEff] = useState<Effective | null>(null)
  const [lib, setLib] = useState<LibraryPolicy[]>([])
  const [preview, setPreview] = useState<PreviewResult[] | null>(null)
  const [watch, setWatch] = useState<Watch | null>(null)
  const [limit, setLimit] = useState('25.00')

  const [f, setF] = useState({
    pickup: 'Depot', dropoff: 'Customer', goods: '200.00', price: '20.00', fee: '5.00',
    deadline: '2030-01-01T12:00', insurance: false,
  })
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({})

  const run = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(msg(e) + (e instanceof JourneyApiError && e.status === 409 ? ' (someone changed this, reloaded)' : ''))
    } finally {
      setBusy(false)
    }
  }, [])

  const submitJob = () => {
    const err: Record<string, string> = {}
    const goods = toMinor(f.goods), price = toMinor(f.price), fee = toMinor(f.fee)
    if (!f.pickup.trim()) err.pickup = 'Required'
    if (!f.dropoff.trim()) err.dropoff = 'Required'
    if (goods === null) err.goods = 'Enter an amount like 200.00'
    if (price === null || price === 0) err.price = 'Enter an amount above zero'
    if (fee === null) err.fee = 'Enter an amount like 5.00'
    if (!f.deadline || Number.isNaN(Date.parse(f.deadline))) err.deadline = 'Pick a deadline'
    setFieldErr(err)
    if (Object.keys(err).length) return
    const draft: JobDraft = {
      pickup: f.pickup.trim(), dropoff: f.dropoff.trim(), goods_value_minor: goods!, price_minor: price!,
      delivery_fee_minor: fee!, deadline: new Date(f.deadline).toISOString(), insurance_required: f.insurance,
    }
    void run(async () => {
      const j = await c.createJob(draft)
      setJob(j)
      const [l, e] = await Promise.all([c.library(), c.effective(j.job_id)])
      setLib(l)
      setEff(e)
      setPreview(null)
      setStep(1)
    })
  }

  const attach = (p: LibraryPolicy) => {
    if (!job || !eff) return
    let params: Record<string, unknown> | undefined
    if (p.parameter) {
      const m = toMinor(limit)
      if (m === null || m === 0) { setError('Enter an approval limit like 25.00'); return }
      params = { [p.parameter.name]: m }
    }
    void run(async () => {
      try {
        setEff(await c.attach(job.job_id, p.policy_id, eff.target_version, params))
      } catch (e) {
        setEff(await c.effective(job.job_id))
        throw e
      }
      setPreview(null)
    })
  }

  const doPreview = () => job && run(async () => { setPreview(await c.preview(job.job_id)); setStep(2) })
  const doDispatch = () => job && run(async () => { setJob(await c.dispatch(job.job_id)); setStep(3) })

  // live watch: poll once a second until the story is finished
  const stop = useRef(false)
  useEffect(() => {
    if (step !== 3 || !job) return
    stop.current = false
    let timer: ReturnType<typeof setTimeout>
    const tick = async () => {
      try {
        const w = await c.watch(job.job_id)
        if (stop.current) return
        setWatch(w)
        setError(null)
        if (w.done) return
      } catch (e) {
        if (!stop.current) setError(`Watching failed, retrying: ${msg(e)}`)
      }
      if (!stop.current) timer = setTimeout(() => void tick(), pollMs)
    }
    void tick()
    return () => { stop.current = true; clearTimeout(timer) }
  }, [step, job, c, pollMs])

  const attachedIds = new Set(eff?.rules.map((r) => r.policy_id))
  const cur = job?.currency ?? 'usd'

  return (
    <section className="journey" aria-labelledby="jr-h">
      <div className="journey-head">
        <h2 id="jr-h">New delivery job</h2>
        <ModeBadges mode={job?.mode ?? 'mock'} />
        {c.kind === 'fixture' && <span className="banner" role="note">Fixture mode: no backend, nothing is paid</span>}
        {onExit && <button type="button" className="quiet" onClick={onExit} style={{ marginLeft: 'auto' }}>Close</button>}
      </div>
      <ol className="journey-steps" aria-label="Progress">
        {STEPS.map((s, i) => (
          <li key={s} aria-current={i === step ? 'step' : undefined} className={i < step ? 'done' : ''}>{s}</li>
        ))}
      </ol>
      {error && <div role="alert" className="error inline">{error}</div>}

      {step === 0 && (
        <form className="jpanel" onSubmit={(e) => { e.preventDefault(); submitJob() }} noValidate>
          <h3>Define the job</h3>
          <div className="jgrid">
            <Field id="pickup" label="Pickup" err={fieldErr.pickup}><input id="pickup" value={f.pickup} onChange={(e) => setF({ ...f, pickup: e.target.value })} /></Field>
            <Field id="dropoff" label="Drop-off" err={fieldErr.dropoff}><input id="dropoff" value={f.dropoff} onChange={(e) => setF({ ...f, dropoff: e.target.value })} /></Field>
            <Field id="goods" label="Goods value (USD)" err={fieldErr.goods}><input id="goods" inputMode="decimal" value={f.goods} onChange={(e) => setF({ ...f, goods: e.target.value })} /></Field>
            <Field id="price" label="Price (USD)" err={fieldErr.price}><input id="price" inputMode="decimal" value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} /></Field>
            <Field id="fee" label="Delivery fee (USD)" err={fieldErr.fee}><input id="fee" inputMode="decimal" value={f.fee} onChange={(e) => setF({ ...f, fee: e.target.value })} /></Field>
            <Field id="deadline" label="Deadline" err={fieldErr.deadline}><input id="deadline" type="datetime-local" value={f.deadline} onChange={(e) => setF({ ...f, deadline: e.target.value })} /></Field>
          </div>
          <label><input type="checkbox" checked={f.insurance} onChange={(e) => setF({ ...f, insurance: e.target.checked })} style={{ minHeight: 0 }} /> Insurance required</label>
          <p className="hint">Currency comes from the organisation policy (CURRENCY-1). Amounts are stored as whole cents.</p>
          <div className="jactions"><span /><button type="submit" className="primary" disabled={busy}>Save and choose policies</button></div>
        </form>
      )}

      {step === 1 && job && (
        <div className="jtwo">
          <div className="jpanel">
            <h3>Attach policies</h3>
            <p className="hint">Job {job.job_id}: {job.pickup} to {job.dropoff}, total {money(job.total_minor, cur)}.</p>
            <ul className="jlib">
              {lib.map((p) => (
                <li key={p.policy_id}>
                  <div className="jrow">
                    <strong>{p.title}</strong>
                    {p.locked ? <span className="badge party">MANDATORY</span> : attachedIds.has(p.policy_id) ? <span className="badge party">ATTACHED</span> : (
                      <button type="button" disabled={busy} onClick={() => attach(p)} aria-label={`Attach ${p.title}`}>Attach</button>
                    )}
                  </div>
                  <span className="hint">{p.summary}</span>
                  {p.parameter && !p.locked && !attachedIds.has(p.policy_id) && (
                    <label>Limit (USD)<input aria-label="Approval limit" value={limit} onChange={(e) => setLimit(e.target.value)} /></label>
                  )}
                </li>
              ))}
            </ul>
            <div className="jactions">
              <button type="button" onClick={() => setStep(0)}>Back</button>
              <button type="button" className="primary" disabled={busy} onClick={() => void doPreview()}>Preview</button>
            </div>
          </div>
          <EffectivePanel eff={eff} />
        </div>
      )}

      {step === 2 && job && (
        <div className="jtwo">
          <div className="jpanel">
            <h3>Preview: what would happen</h3>
            <p className="hint"><ModeBadges mode={job.mode} /> A dry run. Nothing is saved, sent or paid.</p>
            <table className="jtable">
              <thead><tr><th>Situation</th><th>Outcome</th><th>Rule</th><th>Why</th></tr></thead>
              <tbody>
                {(preview ?? []).map((r) => (
                  <tr key={r.situation} data-testid={`preview-${r.situation}`}>
                    <td>{r.label}</td>
                    <td><span className={`vchip v-${r.verdict}`}>{r.verdict}</span></td>
                    <td>{r.clause ? <code>{r.clause}</code> : '-'}</td>
                    <td>{r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="jactions">
              <button type="button" onClick={() => setStep(1)}>Back</button>
              <button type="button" className="primary" disabled={busy} onClick={() => void doDispatch()}>Dispatch job</button>
            </div>
          </div>
          <EffectivePanel eff={eff} />
        </div>
      )}

      {step === 3 && job && (
        <div className="jpanel" aria-live="polite">
          <h3>Dispatched: {job.job_id}</h3>
          <p className="hint">Policies are frozen. Status: <span className={`vchip v-${watch?.status === 'paid' ? 'allow' : watch?.status === 'denied' ? 'deny' : 'hold'}`} data-testid="watch-status">{watch?.status ?? 'starting'}</span>{' '}
            {watch && !watch.done && 'Updating every second.'}
          </p>
          <ol className="jnarr" data-testid="narration">
            {(watch?.steps ?? []).map((s) => (
              <li key={s.seq}>
                <span className="t">{s.text}</span>
                <ModeBadges mode={s.mode} simulated={s.simulated} />
                <span className="badge party">{s.party ?? s.actor}</span>
              </li>
            ))}
            {!watch && <li>Waiting for the first update...</li>}
          </ol>
        </div>
      )}
    </section>
  )
}

function Field({ id, label, err, children }: { id: string; label: string; err?: string; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      {children}
      {err && <span className="jfield-err" role="note">{err}</span>}
    </div>
  )
}
