import { ApiError } from '../api/errors'
import { SITUATIONS } from '../policies/situations'
import { defaultFetch, jsonInit, readWire } from '../lib/wire'
import type { FetchImpl } from '../lib/wire'
import { sentenceFor, verdictFor } from './sentence'
import type { AuthorClient, DraftResult, DryRunRow, PublishedBundle, ValidateRequest, ValidateResult, Verdict, WireClause } from './types'

const party = { 'X-Party-Role': 'admin', 'X-Party-Id': 'console' }
const actor = { 'X-Actor-Role': 'approver', 'X-Actor-Id': 'console' }

/** Backend situation ids -> the ids used by policies/situations.ts. */
export const ID_MAP: Record<string, string> = { all_in_order: 'ok', receipt_missing: 'receipt', prohibited_zone: 'zone', over_limit: 'limit', wrong_currency: 'currency' }
export const mapDryRun = (rows: DryRunRow[]): DryRunRow[] => rows.map((r) => ({ ...r, situation: ID_MAP[r.situation] ?? r.situation }))

export function createAuthorHttpClient(base = '/api/author', fetchImpl: FetchImpl = defaultFetch): AuthorClient {
  const call = <T,>(method: string, path: string, body?: unknown, extra: Record<string, string> = {}) => {
    const init = jsonInit(method, body)
    return fetchImpl(base + path, { ...init, headers: { ...(init.headers as Record<string, string>), ...party, ...extra } }).then((r) => readWire<T>(r))
  }
  return {
    kind: 'http',
    kinds: async () => {
      const d = await call<unknown>('GET', '/clause-kinds')
      const list = Array.isArray(d) ? d : []
      return list.map((k) => (typeof k === 'string' ? k : String((k as { kind?: string }).kind ?? '')))
    },
    draft: async (description, basePolicyId) => {
      const d = await call<DraftResult>('POST', '/draft', { description, base_policy_id: basePolicyId })
      return { ...d, unsupported: d.unsupported ?? [] }
    },
    validate: (req) => call<ValidateResult>('POST', '/validate', req),
    dryRun: async (clauses) => mapDryRun((await call<{ results: DryRunRow[] }>('POST', '/dry-run', { clauses })).results ?? []),
    publish: (req) => call<PublishedBundle>('POST', '/publish', req, actor),
  }
}

/* ---------------- fixture ---------------- */
const MANDATORY = new Set(['org-base'])
const published = new Map<string, { version: number; clauses: WireClause[] }>()
export const FIXTURE_MODEL = 'Example draft (fixture)'

const rank = (c: WireClause) => JSON.stringify(c.params)
function weakensOf(before: WireClause[], after: WireClause[]): string[] {
  const out: string[] = []
  for (const b of before) {
    const a = after.find((x) => x.id === b.id)
    if (!a) out.push(`Removing "${sentenceFor(b)}" lets payments through that were held or denied.`)
    else if (b.kind === 'max_amount' && Number(a.params.limit_minor) > Number(b.params.limit_minor)) out.push(`Raising the limit lets larger payments through without approval.`)
    else if (b.kind === 'allowed_currency' && (a.params.currencies as string[]).length > (b.params.currencies as string[]).length) out.push('Allowing more currencies loosens this rule.')
    else if (b.kind === 'allowed_counterparties' && ((a.params.recipients as string[]) ?? []).length + ((a.params.categories as string[]) ?? []).length > ((b.params.recipients as string[]) ?? []).length + ((b.params.categories as string[]) ?? []).length) out.push('Allowing more payees loosens this rule.')
    else if (b.kind === 'velocity_limit' && (Number(a.params.max_payments) > Number(b.params.max_payments) || Number(a.params.window_seconds) < Number(b.params.window_seconds))) out.push('A higher count or shorter window lets more payments through.')
    else if (b.kind === 'payment_freeze' && b.params.active && !a.params.active) out.push('Unfreezing payments can only be done by an approver from the app bar.')
  }
  return out
}

export function fixtureValidate(req: ValidateRequest): ValidateResult {
  const base = published.get(req.policy_id)
  const before = base?.clauses ?? []
  const errors: ValidateResult['errors'] = []
  for (const c of req.clauses) {
    const p = c.params
    if (c.kind === 'max_amount' && !(Number(p.limit_minor) > 0)) errors.push({ clause: c.id, message: 'Enter an amount above $0.00.' })
    if ((c.kind === 'require_input' || c.kind === 'forbid_input') && !p.key) errors.push({ clause: c.id, message: 'Choose what this rule checks.' })
    if (c.kind === 'allowed_counterparties' && !((p.recipients as string[] | undefined)?.length || (p.categories as string[] | undefined)?.length)) errors.push({ clause: c.id, message: 'List at least one payee or category.' })
    if (c.kind === 'velocity_limit' && !(Number(p.max_payments) >= 1 && Number(p.window_seconds) >= 1)) errors.push({ clause: c.id, message: 'Enter a count and a window of at least 1.' })
    if (c.kind === 'allowed_currency' && !(p.currencies as string[] | undefined)?.length) errors.push({ clause: c.id, message: 'Pick at least one currency.' })
  }
  if (!req.policy_id.trim()) errors.push({ clause: '', message: 'Give the policy an id.' })
  if (!req.clauses.length) errors.push({ clause: '', message: 'Add at least one rule.' })
  const weakens = weakensOf(before, req.clauses)
  const mandatory = MANDATORY.has(req.policy_id)
  if (mandatory && weakens.length) errors.push({ clause: '', message: 'This policy is mandatory for the whole organisation, so it cannot be made weaker.' })
  const diff: ValidateResult['diff'] = {
    added: req.clauses.filter((c) => !before.some((b) => b.id === c.id)).map(sentenceFor),
    removed: before.filter((b) => !req.clauses.some((c) => c.id === b.id)).map(sentenceFor),
    changed: req.clauses.flatMap((c) => {
      const b = before.find((x) => x.id === c.id)
      return b && rank(b) !== rank(c) ? [{ id: c.id, before: sentenceFor(b), after: sentenceFor(c) }] : []
    }),
  }
  return {
    valid: errors.length === 0, errors, weakens, diff, mandatory, current_version: base?.version ?? 0,
    sentences: req.clauses.map((c) => ({ id: c.id, text: sentenceFor(c), verdict_if_unmet: verdictFor(c) })),
  }
}

function fixtureDryRun(clauses: WireClause[]): DryRunRow[] {
  return SITUATIONS.map((s) => {
    const inp = s.inputs as Record<string, unknown>
    let verdict: Verdict = 'allow'
    let hit: WireClause | undefined
    for (const c of clauses) {
      const p = c.params
      let bad = false
      if (c.kind === 'max_amount') bad = s.amountMinor > Number(p.limit_minor)
      else if (c.kind === 'require_input') bad = !inp[String(p.key)]
      else if (c.kind === 'forbid_input') bad = !!inp[String(p.key)]
      else if (c.kind === 'allowed_currency') bad = !((p.currencies as string[]) ?? []).map((x) => x.toLowerCase()).includes(String(inp.currency))
      else if (c.kind === 'allowed_counterparties') {
        const r = ((p.recipients as string[]) ?? []), cat = ((p.categories as string[]) ?? [])
        bad = (r.length > 0 && !r.includes(String(inp.recipient ?? ''))) || (cat.length > 0 && !cat.includes(String(inp.merchant_category ?? '')))
      } else if (c.kind === 'payment_freeze') bad = !!p.active
      if (bad && (verdictFor(c) === 'deny' || verdict === 'allow')) { hit = c; verdict = verdictFor(c); if (verdict === 'deny') break }
    }
    return { situation: s.id, label: s.label, verdict, clause: hit?.id ?? null, reason: hit ? sentenceFor(hit) : 'Every rule is met' }
  })
}

export function fixtureDraft(description: string): DraftResult {
  const d = description.toLowerCase()
  const clauses: WireClause[] = []
  const unsupported: DraftResult['unsupported'] = []
  const add = (kind: string, params: Record<string, unknown>) => {
    const c = { id: `R-${clauses.length + 1}`, prose: '', kind, params }
    clauses.push({ ...c, prose: sentenceFor(c) })
  }
  if (/zone/.test(d)) add('forbid_input', { key: 'prohibited_zone' })
  if (/receipt/.test(d)) add('require_input', { key: 'receipt_confirmed' })
  if (/insur/.test(d)) add('require_input', { key: 'insured' })
  if (/\busd\b|currenc|dollars only/.test(d)) add('allowed_currency', { currencies: ['USD'] })
  const m = d.match(/(?:over|above|more than)\s*\$?(\d+(?:\.\d{1,2})?)/)
  if (m) add('max_amount', { limit_minor: Math.round(parseFloat(m[1]) * 100), currency: 'USD' })
  if (/meal|allowance|up to \$\d+ for/.test(d)) unsupported.push({ text: 'Give drivers up to $12 for a meal on trips over 4 hours', reason: 'Allowances for a purpose and trip length cannot be expressed yet. Rules can only limit amounts, require or forbid something, or restrict currencies.' })
  if (!clauses.length && !unsupported.length) unsupported.push({ text: description.trim().slice(0, 140), reason: 'The example drafter only recognises zones, receipts, insurance, currencies and amount limits.' })
  return { available: true, draft: { policy_id: 'delivery-travel', title: 'Delivery travel policy', clauses }, unsupported, model: FIXTURE_MODEL, ai_drafted: true }
}

export function createAuthorFixtureClient(): AuthorClient {
  return {
    kind: 'fixture',
    kinds: async () => ['max_amount', 'require_input', 'forbid_input', 'allowed_currency', 'allowed_counterparties', 'velocity_limit', 'payment_freeze'],
    draft: async (desc) => fixtureDraft(desc),
    validate: async (r) => fixtureValidate(r),
    dryRun: async (c) => fixtureDryRun(c),
    publish: async (r) => {
      const v = fixtureValidate(r)
      if (!v.valid) throw new ApiError(422, { code: 'invalid', message: v.errors[0]?.message ?? 'Policy is not valid' })
      if (r.expected_version !== v.current_version) throw new ApiError(409, { code: 'stale_revision', message: 'Someone published a newer version' })
      published.set(r.policy_id, { version: v.current_version + 1, clauses: r.clauses })
      return { policy_id: r.policy_id, version: v.current_version + 1, title: r.title, content_hash: `fixture${(r.clauses.length * 7919 + v.current_version * 31).toString(16).padStart(8, '0')}abcd` }
    },
  }
}

/** Fixture when VITE_API_MODE=fixture; otherwise live, falling back to fixture if the backend has no author routes (404). */
export function authorClientFromEnv(mode: string | undefined = import.meta.env.VITE_API_MODE): AuthorClient {
  if (mode === 'fixture') return createAuthorFixtureClient()
  const http = createAuthorHttpClient()
  const fx = createAuthorFixtureClient()
  let active: AuthorClient | null = null
  const resolve = async () => {
    if (active) return active
    try {
      await http.kinds()
      active = http
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) active = fx
      else throw e
    }
    return active
  }
  return {
    get kind() { return active?.kind ?? 'http' },
    kinds: async () => (await resolve()).kinds(),
    draft: async (d, b) => (await resolve()).draft(d, b),
    validate: async (r) => (await resolve()).validate(r),
    dryRun: async (c) => (await resolve()).dryRun(c),
    publish: async (r) => (await resolve()).publish(r),
  }
}
