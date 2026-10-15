import { useEffect, useState } from 'react'
import './memory.css'
import { memoryClientFromEnv } from './client'
import type { MemoryAnswer, MemoryClient, MemoryStatus } from './client'

export const EXAMPLES = [
  'Which clause blocks payment when the route enters a prohibited zone?',
  'What currencies may we settle in, and which clause says so?',
  'Is there a rule that freezes payments?',
]
const msg = (e: unknown) => (e instanceof Error ? e.message : String(e))
const HINT = 'Install the cognee package and set COGNEE_SERVICE_URL with COGNEE_API_KEY, or LLM_API_KEY for a local instance.'

export function MemoryPanel({ client }: { client?: MemoryClient }) {
  const [c, setC] = useState<MemoryClient | null>(client ?? null)
  const [status, setStatus] = useState<MemoryStatus | null>(null)
  const [statusErr, setStatusErr] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [asking, setAsking] = useState(false)
  const [answer, setAnswer] = useState<MemoryAnswer | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    ;(async () => {
      const cl = client ?? (await memoryClientFromEnv())
      if (!live) return
      setC(cl)
      try { const s = await cl.status(); if (live) setStatus(s) } catch (e) { if (live) setStatusErr(msg(e)) }
    })()
    return () => { live = false }
  }, [client])

  const refresh = () => c?.status().then(setStatus).catch(() => undefined)

  async function ask(question: string) {
    if (!c || !question.trim()) return
    setQ(question); setAsking(true); setError(null)
    try { setAnswer(await c.explain(question)) } catch (e) { setAnswer(null); setError(`Could not ask the policy memory: ${msg(e)}`) } finally { setAsking(false) }
  }
  async function sync() {
    if (!c) return
    setSyncing(true); setSyncMsg(null)
    try {
      const r = await c.sync()
      const n = r.written ?? r.count ?? 0
      setSyncMsg(`Wrote ${n} decision${n === 1 ? '' : 's'} to Cognee.`)
      void refresh()
    } catch (e) { setSyncMsg(`Sync failed: ${msg(e)}`) } finally { setSyncing(false) }
  }

  const connected = !!status && status.ready && status.reachable !== false
  const fromCognee = answer?.source === 'cognee'

  return (
    <section className="mem" aria-labelledby="mem-h">
      <h3 id="mem-h">Policy memory (Cognee)</h3>
      <p className="mem-status" role="status" data-ok={connected}>
        {!status && !statusErr && 'Checking memory...'}
        {statusErr && `Not connected: ${statusErr}`}
        {status && connected && `Connected, ${status.mode ?? 'local'} mode`}
        {status && !connected && `Not connected. ${status.message ?? ''}`}
      </p>
      {status && !connected && <p className="mem-hint">{HINT} Answers fall back to the audit log.</p>}
      {status?.last_write?.ref && <p className="mem-ref">Last write: <code>{status.last_write.ref}</code></p>}
      <form className="mem-ask" onSubmit={(e) => { e.preventDefault(); void ask(q) }}>
        <label htmlFor="mem-q">Ask the policy memory</label>
        <div className="mem-row">
          <input id="mem-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. Is there a rule that freezes payments?" />
          <button type="submit" disabled={asking || !q.trim()}>{asking ? 'Asking...' : 'Ask'}</button>
        </div>
        <div className="mem-chips" role="group" aria-label="Example questions">
          {EXAMPLES.map((x) => <button key={x} type="button" className="mem-chip" onClick={() => void ask(x)}>{x}</button>)}
        </div>
      </form>
      {error && <p role="alert" className="error inline">{error}</p>}
      <div aria-live="polite">
        {answer && (
          <div className="mem-answer">
            <span className="mem-badge" data-source={fromCognee ? 'cognee' : 'audit'}>
              {fromCognee ? 'Answered from Cognee memory' : 'Answered from the audit log (Cognee unavailable)'}
            </span>
            <p className="mem-clauses">
              {answer.clause_ids.length ? answer.clause_ids.map((id) => <span key={id} className="mem-clause">{id}</span>) : <em>No clause matched</em>}
            </p>
            {answer.evidence.map((t, i) => <p key={i} className="mem-evidence">{t}</p>)}
            {(answer.policy_id || answer.policy_version != null) && (
              <p className="mem-meta">Policy {answer.policy_id ?? ''}{answer.policy_version != null ? ` v${answer.policy_version}` : ''}</p>
            )}
            {answer.note && <p className="mem-meta">{answer.note}</p>}
          </div>
        )}
      </div>
      <div className="mem-sync">
        <button type="button" onClick={() => void sync()} disabled={syncing || !c}>{syncing ? 'Syncing...' : 'Sync decisions to Cognee'}</button>
        {syncMsg && <span role="status"> {syncMsg}</span>}
      </div>
    </section>
  )
}
