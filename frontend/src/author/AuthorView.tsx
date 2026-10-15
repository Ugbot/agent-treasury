import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError } from '../api/errors'
import { VERDICT_LABEL } from '../policies/clauses'
import { SITUATIONS } from '../policies/situations'
import { authorClientFromEnv } from './client'
import { CURRENCIES, INPUT_KEYS, KIND_OPTIONS, defaultClause, dollarsToMinor, inputKeyLabel, kindLabel, minorToDollars, nextClauseId, parseList, sentenceFor, verdictFor } from './sentence'
import type { AuthorClient, AuthorClause, DryRunRow, PublishedBundle, ValidateResult } from './types'
import './author.css'

export const NOT_CONNECTED = 'AI drafting is not connected. Set ANTHROPIC_API_KEY. You can still write rules by hand.'
const msg = (e: unknown) => (e instanceof Error ? e.message : String(e))

function AmountField({ clause, onChange }: { clause: AuthorClause; onChange: (p: Record<string, unknown>) => void }) {
  const cur = String(clause.params.currency ?? 'USD').toUpperCase()
  const [text, setText] = useState(minorToDollars(Number(clause.params.limit_minor) || 0))
  const bad = dollarsToMinor(text) === null
  return (
    <div className="au-field">
      <label htmlFor={`amt-${clause.id}`}>Limit the amount</label>
      <div className="au-money">
        <span aria-hidden="true">$</span>
        <input id={`amt-${clause.id}`} inputMode="decimal" value={text} aria-invalid={bad} aria-describedby={`amt-${clause.id}-u`}
          onChange={(e) => {
            setText(e.target.value)
            const m = dollarsToMinor(e.target.value)
            if (m !== null) onChange({ limit_minor: m })
          }} />
        <span id={`amt-${clause.id}-u`} className="au-unit">{cur}, dollars and cents</span>
      </div>
      {bad && <p className="au-hint" role="note">Enter an amount like 50 or 12.50.</p>}
    </div>
  )
}

function ClauseEditor({ c, onChange }: { c: AuthorClause; onChange: (p: Record<string, unknown>) => void }) {
  if (c.kind === 'max_amount') return <AmountField clause={c} onChange={onChange} />
  if (c.kind === 'require_input' || c.kind === 'forbid_input') {
    const key = String(c.params.key ?? '')
    const opts = INPUT_KEYS.some((i) => i.key === key) || !key ? INPUT_KEYS : [...INPUT_KEYS, { key, label: inputKeyLabel(key) }]
    return (
      <div className="au-field">
        <label htmlFor={`key-${c.id}`}>{c.kind === 'require_input' ? 'Wait until this is true' : 'Deny when this is true'}</label>
        <select id={`key-${c.id}`} value={key} onChange={(e) => onChange({ key: e.target.value })}>
          {!key && <option value="">Choose one</option>}
          {opts.map((i) => <option key={i.key} value={i.key}>{i.label}</option>)}
        </select>
      </div>
    )
  }
  if (c.kind === 'allowed_currency') {
    const cur = ((c.params.currencies as string[]) ?? []).map((x) => x.toUpperCase())
    const all = [...new Set([...CURRENCIES, ...cur])]
    return (
      <fieldset className="au-field au-cur">
        <legend>Currencies allowed</legend>
        {all.map((x) => (
          <label key={x} className="au-cb">
            <input type="checkbox" checked={cur.includes(x)} onChange={(e) => onChange({ currencies: e.target.checked ? [...cur, x] : cur.filter((y) => y !== x) })} />
            {x}
          </label>
        ))}
      </fieldset>
    )
  }
  if (c.kind === 'allowed_counterparties') {
    const join = (v: unknown) => (Array.isArray(v) ? v.join(', ') : '')
    const bad = !parseList(join(c.params.recipients)).length && !parseList(join(c.params.categories)).length
    return (
      <div className="au-field">
        <label htmlFor={`rcp-${c.id}`}>Payees allowed (separate with commas)</label>
        <input id={`rcp-${c.id}`} defaultValue={join(c.params.recipients)} onChange={(e) => onChange({ recipients: parseList(e.target.value) })} />
        <label htmlFor={`cat-${c.id}`}>Merchant categories allowed (separate with commas)</label>
        <input id={`cat-${c.id}`} defaultValue={join(c.params.categories)} onChange={(e) => onChange({ categories: parseList(e.target.value) })} />
        {bad && <p className="au-hint" role="note">List at least one payee or category.</p>}
      </div>
    )
  }
  if (c.kind === 'velocity_limit') {
    const secs = Number(c.params.window_seconds) || 60
    const mins = secs % 60 === 0
    return (
      <div className="au-field">
        <label htmlFor={`vn-${c.id}`}>Most payments allowed</label>
        <input id={`vn-${c.id}`} type="number" min={1} value={Number(c.params.max_payments) || 1} onChange={(e) => onChange({ max_payments: Math.max(1, parseInt(e.target.value, 10) || 1) })} />
        <label htmlFor={`vw-${c.id}`}>Every how many minutes</label>
        <input id={`vw-${c.id}`} type="number" min={1} value={mins ? secs / 60 : Math.ceil(secs / 60)} onChange={(e) => onChange({ window_seconds: Math.max(1, parseInt(e.target.value, 10) || 1) * 60 })} />
      </div>
    )
  }
  if (c.kind === 'payment_freeze') {
    return <p className="au-hint">Freezing and unfreezing is done with the Freeze payments control in the app bar, by an approver.</p>
  }
  return <p className="au-hint">This rule type cannot be edited here. Remove it and add it again.</p>
}

function ClauseCard({ c, index, error, onChange, onRemove }: { c: AuthorClause; index: number; error?: string; onChange: (p: Record<string, unknown>) => void; onRemove: () => void }) {
  const v = verdictFor(c)
  return (
    <li className="au-clause" data-invalid={!!error}>
      <p className="au-sentence">{sentenceFor(c)}</p>
      <div className="au-meta">
        <span className="pol-tag">{c.id}</span>
        <span className={`pol-chip v-${v}`}>If not met: {VERDICT_LABEL[v]}</span>
        <span className="au-kind">{kindLabel(c.kind)}</span>
        {c.ai && <span className="au-ai">AI drafted</span>}
      </div>
      {error && <p className="au-err" role="note">{error}</p>}
      <details className="au-edit">
        <summary>Edit rule {index + 1}</summary>
        <ClauseEditor c={c} onChange={onChange} />
        <button type="button" className="quiet au-remove" onClick={onRemove} aria-label={`Remove rule ${c.id}`}>Remove</button>
      </details>
    </li>
  )
}

const asNum = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`
export function summarize(v: ValidateResult): string {
  const parts: string[] = []
  if (v.diff.added.length) parts.push(`adds ${asNum(v.diff.added.length, 'rule', 'rules')}`)
  if (v.diff.removed.length) parts.push(`removes ${asNum(v.diff.removed.length, 'rule', 'rules')}`)
  if (v.diff.changed.length) parts.push(`${v.weakens.length ? 'changes' : 'tightens'} ${v.diff.changed.length}`)
  const body = parts.length ? parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0] : 'changes nothing'
  return `This ${body}. It applies to payments you attach it to.`
}

export function AuthorView({ client, debounceMs = 400, onOpenPolicies }: { client?: AuthorClient; debounceMs?: number; onOpenPolicies?: () => void }) {
  const c = useMemo(() => client ?? authorClientFromEnv(), [client])
  const [policyId, setPolicyId] = useState('my-policy')
  const [title, setTitle] = useState('New policy')
  const [clauses, setClauses] = useState<AuthorClause[]>([])
  const [desc, setDesc] = useState('')
  const [drafting, setDrafting] = useState(false)
  const abort = useRef<{ stopped: boolean } | null>(null)
  const [aiUp, setAiUp] = useState<boolean | null>(null)
  const [draftErr, setDraftErr] = useState<string | null>(null)
  const [unsupported, setUnsupported] = useState<{ text: string; reason: string }[]>([])
  const [meta, setMeta] = useState<{ prompt: string; model?: string } | null>(null)
  const [fixture, setFixture] = useState(false)
  const [check, setCheck] = useState<ValidateResult | null>(null)
  const [fresh, setFresh] = useState(false)
  const [checkErr, setCheckErr] = useState<string | null>(null)
  const [dry, setDry] = useState<DryRunRow[]>([])
  const [dryErr, setDryErr] = useState(false)
  const confirmRef = useRef<HTMLButtonElement>(null)
  const doneRef = useRef<HTMLDivElement>(null)
  const goRef = useRef<HTMLButtonElement>(null)
  const [confirming, setConfirming] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [done, setDone] = useState<PublishedBundle | null>(null)
  const [pubErr, setPubErr] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  useEffect(() => { if (confirming) confirmRef.current?.focus() }, [confirming])
  useEffect(() => { if (done) (goRef.current ?? doneRef.current)?.focus() }, [done])
  const [reload, setReload] = useState(0)
  const [addKind, setAddKind] = useState(KIND_OPTIONS[0].kind)

  useEffect(() => { c.kinds().then(() => setFixture(c.kind === 'fixture')).catch(() => setFixture(c.kind === 'fixture')) }, [c])

  const edit = () => { setConfirming(false); setDone(null); setPubErr(null); setConflict(false) }
  const patch = useCallback((id: string, params: Record<string, unknown>) => {
    setClauses((cs) => cs.map((x) => {
      if (x.id !== id) return x
      const n = { ...x, params: { ...x.params, ...params }, ai: false }
      return { ...n, prose: sentenceFor(n) }
    }))
    edit()
  }, [])

  // Live check: debounced; stale responses are dropped.
  useEffect(() => {
    setFresh(false)
    if (!clauses.length) { setCheck(null); setDry([]); setCheckErr(null); return }
    let live = true
    const t = setTimeout(() => {
      const req = { policy_id: policyId, title, clauses: clauses.map(({ ai: _a, ...w }) => w) }
      Promise.allSettled([c.validate(req), c.dryRun(req.clauses)]).then(([v, d]) => {
        if (!live) return
        if (v.status === 'rejected') { setCheckErr(msg(v.reason)); setCheck(null); setDry([]); setDryErr(false); return }
        setCheck(v.value); setCheckErr(null); setFresh(true)
        if (d.status === 'fulfilled') { setDry(d.value); setDryErr(false) } else { setDry([]); setDryErr(true) }
      })
    }, debounceMs)
    return () => { live = false; clearTimeout(t) }
  }, [c, policyId, title, clauses, debounceMs, reload])

  const runDraft = async () => {
    const token = { stopped: false }
    abort.current = token
    setDrafting(true); setDraftErr(null)
    try {
      const r = await c.draft(desc, policyId)
      if (token.stopped) return
      setAiUp(r.available)
      if (!r.available) return
      setUnsupported(r.unsupported ?? [])
      if (r.draft) {
        setClauses(r.draft.clauses.map((x) => ({ ...x, ai: true })))
        if (r.draft.title) setTitle(r.draft.title)
        if (r.draft.policy_id) setPolicyId(r.draft.policy_id)
        setMeta({ prompt: desc, model: r.model })
        edit()
      }
    } catch (e) {
      if (!token.stopped) setDraftErr(msg(e))
    } finally {
      if (abort.current === token) setDrafting(false)
    }
  }
  const stop = () => { if (abort.current) abort.current.stopped = true; setDrafting(false) }

  const version = (check?.current_version ?? 0) + 1
  const blocked = !!check?.mandatory && check.weakens.length > 0
  const canPublish = !!check?.valid && fresh && !blocked && !publishing
  const aiDrafted = !!meta && clauses.length > 0 && clauses.every((x) => x.ai)
  const publish = async () => {
    if (!check) return
    setPublishing(true); setPubErr(null)
    try {
      const b = await c.publish({
        policy_id: policyId, title, clauses: clauses.map(({ ai: _a, ...w }) => w), expected_version: check.current_version,
        ai_drafted: aiDrafted, prompt: aiDrafted ? meta?.prompt : undefined, model: aiDrafted ? meta?.model : undefined,
      })
      setDone(b); setConfirming(false)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setConflict(true)
      else setPubErr(msg(e))
      setConfirming(false)
    } finally { setPublishing(false) }
  }
  const errFor = (id: string) => check?.errors.find((e) => e.clause === id)?.message
  const generalErrors = check?.errors.filter((e) => !e.clause || !clauses.some((x) => x.id === e.clause)) ?? []
  const draftWhy = aiUp === false ? NOT_CONNECTED : !desc.trim() ? 'Describe a rule first.' : null

  return (
    <div className="author">
      <header className="pol-head">
        <h2>Write a policy</h2>
        <p className="pol-lede">Describe a rule in plain English and let the AI draft it, or write rules by hand. Nothing goes live until you publish.</p>
        {fixture && <p className="banner pol-banner" role="note">Fixture mode: canned drafts, nothing is saved to a server</p>}
      </header>
      <div className="au-grid">
        <section className="au-left" aria-labelledby="au-describe">
          <h3 id="au-describe">Describe it</h3>
          <label htmlFor="au-desc">Describe the rule in your own words</label>
          <textarea id="au-desc" rows={6} value={desc} onChange={(e) => setDesc(e.target.value)}
            placeholder="For example: never pay when the route enters a prohibited zone, settle only in USD, hold anything over $50, give drivers up to $12 for a meal on trips over 4 hours" />
          <div className="au-row">
            {drafting
              ? <button type="button" onClick={stop}>Stop drafting</button>
              : <button type="button" className="primary" onClick={() => void runDraft()} disabled={aiUp === false || !desc.trim()} aria-describedby="au-why">Draft with AI</button>}
            {drafting && <span className="au-spin" role="status"><i aria-hidden="true" /> Drafting rules</span>}
          </div>
          {draftWhy && !drafting && <p id="au-why" className="au-hint">{draftWhy}</p>}
          {draftErr && <p className="error inline" role="alert">The AI could not draft this: {draftErr}. Try again, or add rules by hand on the right.</p>}
          {meta && !drafting && <p className="au-hint">{meta.model?.startsWith('Example') ? meta.model : `Drafted by ${meta.model ?? 'the AI agent'}`}. Check each rule before you publish.</p>}
          {unsupported.length > 0 && (
            <div className="au-unsup" role="region" aria-label="Not expressed">
              <h4>The AI could not write these as rules</h4>
              <ul>{unsupported.map((u, i) => <li key={i}><strong>{u.text}</strong><span>{u.reason}</span></li>)}</ul>
            </div>
          )}
        </section>

        <section className="au-right" aria-labelledby="au-policy">
          <h3 id="au-policy">The policy</h3>
          <div className="au-ids">
            <div><label htmlFor="au-title">Title</label><input id="au-title" value={title} onChange={(e) => { setTitle(e.target.value); edit() }} /></div>
            <div><label htmlFor="au-pid">Policy id</label><input id="au-pid" value={policyId} onChange={(e) => { setPolicyId(e.target.value.trim()); edit() }} /></div>
          </div>
          {clauses.length === 0
            ? <p className="pol-empty">No rules yet. Describe one on the left and choose Draft with AI, or choose Add rule below.</p>
            : <ol className="au-clauses" aria-label="Rules">{clauses.map((x, i) => (
                <ClauseCard key={x.id} c={x} index={i} error={errFor(x.id)} onChange={(p) => patch(x.id, p)}
                  onRemove={() => { setClauses((cs) => cs.filter((y) => y.id !== x.id)); edit() }} />))}</ol>}
          <div className="au-add">
            <label htmlFor="au-kind">Kind of rule</label>
            <select id="au-kind" value={addKind} onChange={(e) => setAddKind(e.target.value)}>{KIND_OPTIONS.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}</select>
            <button type="button" onClick={() => { setClauses((cs) => [...cs, defaultClause(addKind, nextClauseId(cs))]); edit() }}>Add rule</button>
          </div>
        </section>
      </div>

      <section className="au-check" aria-labelledby="au-check-h">
        <h3 id="au-check-h">Check</h3>
        <div aria-live="polite" className="au-live">
          {!clauses.length && <p className="au-hint">Add a rule to see the check.</p>}
          {clauses.length > 0 && !check && !checkErr && <p className="au-hint">Checking</p>}
          {checkErr && <p className="error inline" role="alert">The check could not run: {checkErr}. Make sure the API is running on port 8000, then edit any field to retry.</p>}
          {check && (
            <>
              <p className={`au-status ${check.valid ? 'ok' : 'bad'}`}>{check.valid ? 'Valid. Every rule is well formed.' : `Not valid yet. Fix ${asNum(check.errors.length, 'problem', 'problems')} to publish.`}</p>
              {generalErrors.length > 0 && <ul className="au-list bad">{generalErrors.map((e, i) => <li key={i}>{e.message}</li>)}</ul>}
              {check.errors.filter((e) => e.clause && clauses.some((x) => x.id === e.clause)).length > 0 && (
                <ul className="au-list bad">{check.errors.filter((e) => e.clause && clauses.some((x) => x.id === e.clause)).map((e, i) => <li key={i}>Rule {e.clause}: {e.message}</li>)}</ul>)}
              {check.weakens.length > 0 && (
                <div className="au-weak" role="note"><strong>{check.mandatory ? 'Blocked: this makes a mandatory policy weaker' : 'This makes the policy weaker'}</strong>
                  <ul>{check.weakens.map((w, i) => <li key={i}>{w}</li>)}</ul></div>)}
              <div className="au-diff"><h4>{check.current_version ? `Compared with version ${check.current_version}` : 'No earlier version, so everything is new'}</h4>
                {!check.diff.added.length && !check.diff.removed.length && !check.diff.changed.length ? <p className="au-hint">No changes.</p> : (
                  <ul>
                    {check.diff.added.map((t, i) => <li key={`a${i}`} className="add"><b>Added</b> {t}</li>)}
                    {check.diff.removed.map((t, i) => <li key={`r${i}`} className="rem"><b>Removed</b> {t}</li>)}
                    {check.diff.changed.map((d) => <li key={d.id} className="chg"><b>Changed</b> {d.before} <span aria-hidden="true">to</span><span className="sr"> to </span> {d.after}</li>)}
                  </ul>)}</div>
            </>
          )}
        </div>
        {dryErr && <p className="au-hint" role="note">Try it is unavailable right now.</p>}
        {dry.length > 0 && (
          <div className="au-try">
            <h4>Try it <span className="pol-preview">Preview, no money moves</span></h4>
            <ul className="au-pills" aria-live="polite">
              {SITUATIONS.map((s) => { const r = dry.find((d) => d.situation === s.id); return r ? (
                <li key={s.id} className={`au-pill v-${r.verdict}`}><span className="au-pill-s">{s.label}</span><strong>{VERDICT_LABEL[r.verdict]}</strong>
                  <span className="au-pill-r">{r.clause ? `${r.clause}: ${r.reason}` : r.reason}</span></li>) : null })}
            </ul>
          </div>)}
      </section>

      <section className="au-publish" aria-label="Publish">
        <p className="au-hint">The AI only drafts. Publishing is always a separate action by a person.</p>
        {conflict && <div className="error inline" role="alert"><p>Someone published a newer version. Reload to see it, then reapply your edits.</p>
          <button type="button" onClick={() => { setConflict(false); setReload((n) => n + 1) }}>Reload</button></div>}
        {pubErr && <p className="error inline" role="alert">Could not publish: {pubErr}. Your edits are kept; try again.</p>}
        {done ? (
          <div className="au-done" role="status" tabIndex={-1} ref={doneRef}><p><strong>Published version {done.version}</strong> of {done.policy_id}.</p>
            <p className="au-hint">Fingerprint <code>{done.content_hash.slice(0, 12)}</code></p>
            {onOpenPolicies && <button type="button" className="link" ref={goRef} onClick={onOpenPolicies}>Go to Policies to attach it</button>}</div>
        ) : confirming && check ? (
          <div className="au-confirm" role="group" aria-label="Confirm publish"><p>{summarize(check)}</p>
            <div className="au-row"><button type="button" className="primary" ref={confirmRef} onClick={() => void publish()} disabled={publishing}>{publishing ? 'Publishing' : `Confirm and publish version ${version}`}</button>
              <button type="button" className="quiet" onClick={() => setConfirming(false)}>Cancel</button></div></div>
        ) : (
          <div className="au-row"><button type="button" className="primary" disabled={!canPublish} aria-describedby="au-pub-why" onClick={() => setConfirming(true)}>Publish version {version}</button>
            {!canPublish && <span id="au-pub-why" className="au-hint">{blocked ? 'Blocked: a mandatory policy cannot be made weaker.' : !clauses.length ? 'Add a rule first.' : check && !check.valid ? 'Fix the problems above first.' : 'Checking your edits.'}</span>}</div>
        )}
      </section>
    </div>
  )
}
