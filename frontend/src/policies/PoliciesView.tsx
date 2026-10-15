import { useCallback, useEffect, useMemo, useState } from 'react'
import './policies.css'
import { MemoryPanel } from '../memory/MemoryPanel'
import { policiesClientFromEnv } from './client'
import { clauseSentence, evidenceSentence, VERDICT_LABEL } from './clauses'
import { LibraryDetail, LibraryList } from './Library'
import { buildLayers } from './layers'
import { RuleStack, resultKey } from './RuleStack'
import type { ResultMap } from './RuleStack'
import { SITUATIONS, situationInput } from './situations'
import { PolicyApiError } from './types'
import type { Attachment, ClauseOutcome, DecisionOut, Outcome, PoliciesClient, PolicyBundle, TargetRef, TargetType } from './types'

interface Props {
  client?: PoliciesClient
  missionId?: string
  cardId?: string
  paymentId?: string
}

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e))
const RANK: Record<Outcome, number> = { allow: 0, hold: 1, deny: 2 }
const CONFLICT = 'Someone else changed the rules for this target while you were editing. We reloaded the latest version. Check the list, then try again.'
const MEMORY_HINT = 'Install the cognee package and set COGNEE_SERVICE_URL with COGNEE_API_KEY, or LLM_API_KEY for a local instance.'

export function PoliciesView({ client, missionId = 'mission-1', cardId = 'card-1', paymentId = 'payment-1' }: Props) {
  const c = useMemo(() => client ?? policiesClientFromEnv(), [client])
  const chain = useMemo<TargetRef[]>(
    () => [
      { type: 'organisation', id: 'org' },
      { type: 'mission', id: missionId },
      { type: 'card', id: cardId },
      { type: 'payment', id: paymentId },
    ],
    [missionId, cardId, paymentId],
  )
  const [bundles, setBundles] = useState<PolicyBundle[]>([])
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [versions, setVersions] = useState<Record<string, number>>({})
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [situation, setSituation] = useState(SITUATIONS[0].id)
  const [decision, setDecision] = useState<DecisionOut | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [target, setTarget] = useState<TargetType>('payment')

  const load = useCallback(async () => {
    try {
      const [bs, effs] = await Promise.all([c.listPolicies(), Promise.all(chain.map((t) => c.effective(t)))])
      setBundles(bs)
      // effective() returns inherited attachments for every target, so the same one arrives several times.
      const seen = new Map(effs.flatMap((e) => e.attachments).map((a) => [a.attachment_id, a] as const))
      setAttachments([...seen.values()])
      setVersions(Object.fromEntries(chain.map((t, i) => [`${t.type}:${t.id}`, effs[i].target_version])))
      setLoadError(null)
      setStatus('ready')
    } catch (e) {
      setLoadError(`Could not load policies: ${msg(e)}`)
      setStatus('error')
    }
  }, [c, chain])

  useEffect(() => {
    // eslint-disable-next-line -- initial fetch on mount
    void load()
  }, [load])

  // Preview: re-run the real effective rules against the chosen situation. Read-only, no money moves.
  useEffect(() => {
    if (status !== 'ready') return
    let live = true
    const s = SITUATIONS.find((x) => x.id === situation) ?? SITUATIONS[0]
    c.decide(situationInput(s, chain[3], [chain[2], chain[1]]))
      .then((d) => { if (live) { setDecision(d); setPreviewError(null) } })
      .catch((e) => { if (live) setPreviewError(`Preview failed: ${msg(e)}`) })
    return () => { live = false }
  }, [c, chain, status, situation, attachments])

  const layers = useMemo(() => buildLayers(attachments, bundles, chain), [attachments, bundles, chain])
  const totalRules = layers[layers.length - 1].inForce
  const results = useMemo<ResultMap | null>(() => {
    if (!decision) return null
    const m: ResultMap = new Map()
    for (const r of decision.results) if (r.clause) m.set(resultKey(r.policy_id, r.clause), r)
    return m
  }, [decision])

  const paymentLabel = `payment ${paymentId}`
  const verOf = (t: TargetRef) => versions[`${t.type}:${t.id}`] ?? 0
  const selBundle = bundles.filter((b) => b.policy_id === selected).sort((a, b) => b.version - a.version)[0]

  async function run(fn: () => Promise<unknown>) {
    setBusy(true)
    setNotice(null)
    let failure: string | null = null
    try {
      await fn()
    } catch (e) {
      failure = e instanceof PolicyApiError && e.isConflict ? CONFLICT : msg(e)
    }
    await load()
    if (failure) setNotice(failure)
    setBusy(false)
  }
  const attach = () => {
    if (!selBundle) return
    const t = chain.find((x) => x.type === target)!
    return run(() => c.attach(t, selBundle.policy_id, selBundle.version, verOf(t)))
  }
  const detach = (a: Attachment) => run(() => c.detach(a.attachment_id, verOf({ type: a.target_type, id: a.target_id })))

  const memoryDown = bundles.some((b) => b.memory && !b.memory.ok)
  const memoryMsg = bundles.find((b) => b.memory && !b.memory.ok)?.memory?.message

  return (
    <section aria-labelledby="pol-h" className="policies">
      <header className="pol-head">
        <h2 id="pol-h">Policies</h2>
        <p className="pol-lede">Written rules decide every payment. They stack from the organisation down to the single payment, and the strictest result wins.</p>
      </header>
      <MemoryPanel />
      {c.kind === 'fixture' && <p role="note" className="banner pol-banner">Fixture mode: canned policies, no backend</p>}
      {status === 'ready' && memoryDown && (
        <div role="note" className="pol-memory">
          <p><strong>Policy memory: not connected.</strong> Decisions are still enforced from the local ledger.</p>
          <details>
            <summary>Setup hint</summary>
            <p>{MEMORY_HINT}</p>
            {memoryMsg && <p className="hint">{memoryMsg}</p>}
          </details>
        </div>
      )}
      {notice && <div role="alert" className="error pol-alert">{notice}</div>}

      {status === 'loading' && <p role="status" className="pol-loading">Loading policies...</p>}
      {status === 'error' && (
        <div role="alert" className="error pol-alert">
          <p>{loadError}</p>
          <p>Check that the API is running, then retry.</p>
          <button type="button" onClick={() => { setStatus('loading'); void load() }}>Retry</button>
        </div>
      )}
      {status === 'ready' && (
        <div className="pol-grid">
          <div className="pol-main">
            {attachments.length === 0 && (
              <p className="pol-empty">No policy applies to this payment yet. Any payment would be unchecked. Attach one from the library.</p>
            )}
            <RuleStack
              layers={layers}
              results={results}
              selectedPolicyId={selected}
              onSelect={setSelected}
              paymentLabel={paymentLabel}
              totalRules={totalRules}
              verdictSlot={
                <Verdict
                  decision={decision}
                  error={previewError}
                  bundles={bundles}
                  layers={layers}
                  situation={situation}
                  onSituation={setSituation}
                  totalRules={totalRules}
                />
              }
            />
          </div>
          <aside className="pol-side" aria-label="Policy library">
            <h3 className="pol-side-h">Policy library</h3>
            <LibraryList bundles={bundles} attachments={attachments} selectedId={selected} onSelect={setSelected} />
            {selBundle ? (
              <LibraryDetail
                bundle={selBundle} attachments={attachments} chain={chain} target={target} onTarget={setTarget}
                busy={busy} onAttach={() => void attach()} onDetach={(a) => void detach(a)}
              />
            ) : (
              bundles.length > 0 && <p className="hint">Select a policy to read its rules and attach it.</p>
            )}
          </aside>
        </div>
      )}
    </section>
  )
}

function Verdict({ decision, error, bundles, layers, situation, onSituation, totalRules }: {
  decision: DecisionOut | null
  error: string | null
  bundles: PolicyBundle[]
  layers: ReturnType<typeof buildLayers>
  situation: string
  onSituation: (id: string) => void
  totalRules: number
}) {
  const all = layers.flatMap((l) => l.policies.flatMap((p) => p.bundle?.clauses ?? []))
  let deciding: ClauseOutcome | null = null
  if (decision) for (const r of decision.results) if (RANK[r.outcome] > RANK[deciding?.outcome ?? 'allow']) deciding = r
  const clause = deciding ? bundles.find((b) => b.policy_id === deciding!.policy_id)?.clauses.find((x) => x.id === deciding!.clause) : undefined
  const v = decision?.verdict
  return (
    <div className="pol-verdict" data-verdict={v}>
      <div className="pol-verdict-head">
        <h3>Verdict for this payment</h3>
        <span className="pol-preview">Preview, no money moves</span>
      </div>
      <div className="lens pol-situations" role="group" aria-label="Try a situation">
        {SITUATIONS.map((s) => (
          <button key={s.id} type="button" aria-pressed={situation === s.id} onClick={() => onSituation(s.id)}>{s.label}</button>
        ))}
      </div>
      {error && <p role="alert" className="error inline">{error}</p>}
      <div aria-live="polite" className="pol-verdict-out">
        {decision && v ? (
          <>
            <p className={`pol-verdict-word v-${v}`} data-testid="verdict">{VERDICT_LABEL[v]}</p>
            {deciding ? (
              <p className="pol-deciding" data-testid="deciding">
                <code className="pol-tag">{deciding.clause ?? deciding.policy_id}</code>{' '}
                {clause ? `${clauseSentence(clause)}.` : 'A rule decided this.'}{' '}
                <span className="pol-why-line">{evidenceSentence(clause, deciding)}.</span>
              </p>
            ) : (
              <p className="pol-deciding" data-testid="deciding">Every one of the {totalRules} rules is met, so the payment would be released.</p>
            )}
          </>
        ) : !error && <p className="hint">Checking the rules...</p>}
      </div>
      <details className="pol-allrules">
        <summary>All {totalRules} rules in force</summary>
        <ul className="plain">{all.map((c, i) => <li key={`${c.id}-${i}`}>{clauseSentence(c)} <code className="pol-tag">{c.id}</code></li>)}</ul>
      </details>
    </div>
  )
}
