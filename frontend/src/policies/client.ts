import type {
  Attachment, ClauseOutcome, DecideInput, DecisionOut, EffectiveTarget, Outcome, PoliciesClient, PolicyBundle, TargetRef,
} from './types'
import { PolicyApiError } from './types'

type Fetch = typeof fetch

async function parse<T>(res: Response): Promise<T> {
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail
    throw new PolicyApiError(res.status, typeof detail === 'string' ? detail : `Request failed (${res.status})`)
  }
  return body as T
}

/** Talks to the APIRouter in routes_policies.py; `base` is wherever the integrator mounts it. */
export function createPoliciesHttpClient(base = '/api/policies', fetchImpl: Fetch = (...a) => fetch(...a)): PoliciesClient {
  const json = (method: string, path: string, body?: unknown) =>
    fetchImpl(base + path, {
      method,
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  const ref = (t: TargetRef) => `${encodeURIComponent(t.type)}/${encodeURIComponent(t.id)}`
  return {
    kind: 'http',
    listPolicies: () => json('GET', '').then((r) => parse<PolicyBundle[]>(r)),
    effective: (t) => json('GET', `/targets/${ref(t)}/effective`).then((r) => parse<EffectiveTarget>(r)),
    attach: (t, policyId, version, expectedVersion) =>
      json('POST', '/attachments', {
        target_type: t.type, target_id: t.id, policy_id: policyId, policy_version: version, expected_version: expectedVersion,
      }).then((r) => parse<{ target_version: number }>(r)),
    detach: (id, expectedVersion) =>
      json('DELETE', `/attachments/${encodeURIComponent(id)}?expected_version=${expectedVersion}`).then((r) =>
        parse<{ target_version: number }>(r)),
    decide: (d: DecideInput) =>
      json('POST', '/decide', {
        target_type: d.target.type, target_id: d.target.id, inputs: d.inputs, input_revision: d.inputRevision,
        amount_minor: d.amountMinor, inherited: d.inherited.map((x) => [x.type, x.id]),
      }).then((r) => parse<DecisionOut>(r)),
  }
}

const BASE: PolicyBundle = {
  policy_id: 'org-base', version: 1, title: 'Organisation base policy', content_hash: 'fixture-org-base-1',
  clauses: [
    { id: 'CURRENCY-1', prose: "Only settle in the organisation's permitted currencies", kind: 'allowed_currency', params: { currencies: ['usd'] } },
    { id: 'PAY-1', prose: 'Release only after delivery proof', kind: 'require_input', params: { key: 'receipt_confirmed' } },
    { id: 'TRAVEL-1', prose: 'Never pay when route enters a prohibited zone', kind: 'forbid_input', params: { key: 'prohibited_zone' } },
    { id: 'APPROVE-1', prose: 'Amounts above USD 50.00 need approval', kind: 'max_amount', params: { limit_minor: 5000 } },
  ],
}
const CARD_CAP: PolicyBundle = {
  policy_id: 'card-cap', version: 1, title: 'Card spend cap', content_hash: 'fixture-card-cap-1',
  clauses: [{ id: 'CAP-1', prose: 'Card payments above USD 20.00 need approval', kind: 'max_amount', params: { limit_minor: 2000 } }],
}
const INSURED: PolicyBundle = {
  policy_id: 'travel-insured', version: 1, title: 'Insured travel only', content_hash: 'fixture-insured-1',
  clauses: [{ id: 'INS-1', prose: 'Driver must be insured', kind: 'require_input', params: { key: 'insured' } }],
}

/** Explicit fixture mode: in-memory, mirrors the backend's version and mandatory rules. No payment authority. */
export function createPoliciesFixtureClient(): PoliciesClient {
  const bundles = [BASE, CARD_CAP, INSURED]
  const atts = new Map<string, Attachment>()
  const versions = new Map<string, number>()
  let n = 0
  const key = (t: TargetRef) => `${t.type}:${t.id}`
  const add = (t: TargetRef, p: PolicyBundle, mandatory: boolean) => {
    const a: Attachment = {
      attachment_id: `att-${++n}`, target_type: t.type, target_id: t.id, policy_id: p.policy_id, policy_version: p.version, mandatory,
    }
    atts.set(a.attachment_id, a)
    versions.set(key(t), (versions.get(key(t)) ?? 0) + 1)
    return a
  }
  add({ type: 'organisation', id: 'org' }, BASE, true)

  return {
    kind: 'fixture',
    async listPolicies() {
      return bundles
    },
    async effective(t) {
      return {
        target_version: versions.get(key(t)) ?? 0,
        attachments: [...atts.values()].filter((a) => a.target_type === t.type && a.target_id === t.id),
      }
    },
    async attach(t, policyId, version, expected) {
      const p = bundles.find((b) => b.policy_id === policyId && b.version === version)
      if (!p) throw new PolicyApiError(404, `unknown policy ${policyId} v${version}`)
      if ((versions.get(key(t)) ?? 0) !== expected) throw new PolicyApiError(409, 'target version conflict')
      add(t, p, false)
      return { target_version: versions.get(key(t))! }
    },
    async detach(id, expected) {
      const a = atts.get(id)
      if (!a) throw new PolicyApiError(404, 'unknown attachment')
      if (a.mandatory) throw new PolicyApiError(403, 'mandatory policy cannot be detached')
      const k = `${a.target_type}:${a.target_id}`
      if ((versions.get(k) ?? 0) !== expected) throw new PolicyApiError(409, 'target version conflict')
      atts.delete(id)
      versions.set(k, expected + 1)
      return { target_version: expected + 1 }
    },
    async decide(d: DecideInput): Promise<DecisionOut> {
      const chain = [d.target, ...d.inherited]
      const eff = [...atts.values()].filter(
        (a) => a.mandatory || a.target_type === 'organisation' || chain.some((c) => c.type === a.target_type && c.id === a.target_id),
      )
      const rank: Record<Outcome, number> = { allow: 0, hold: 1, deny: 2 }
      let verdict: Outcome = 'allow'
      const results: ClauseOutcome[] = []
      for (const a of eff) {
        const b = bundles.find((x) => x.policy_id === a.policy_id && x.version === a.policy_version)!
        for (const c of b.clauses) {
          let outcome: Outcome = 'allow'
          let evidence: Record<string, unknown> = {}
          const k = c.params.key as string
          if (c.kind === 'max_amount' && d.amountMinor > (c.params.limit_minor as number)) {
            outcome = 'hold'
            evidence = { amount_minor: d.amountMinor, limit_minor: c.params.limit_minor }
          } else if (c.kind === 'require_input' && !d.inputs[k]) {
            outcome = 'hold'
            evidence = { missing: k }
          } else if (c.kind === 'forbid_input' && d.inputs[k]) {
            outcome = 'deny'
            evidence = { present: k }
          } else if (c.kind === 'allowed_currency') {
            const allowed = c.params.currencies as string[]
            const cur = String(d.inputs.currency ?? '').toLowerCase()
            if (!cur) {
              outcome = 'hold'
              evidence = { missing: 'currency' }
            } else if (!allowed.includes(cur)) {
              outcome = 'deny'
              evidence = { currency: cur, allowed }
            }
          }
          results.push({ policy_id: a.policy_id, policy_version: a.policy_version, clause: c.id, outcome, evidence })
          if (rank[outcome] > rank[verdict]) verdict = outcome
        }
      }
      return {
        verdict, input_revision: d.inputRevision, effective_policy_hash: eff.map((a) => a.policy_id).join('+'),
        attachments: eff.map((a) => [a.attachment_id, a.policy_id, a.policy_version, a.mandatory]),
        results, reasons: eff.length ? [] : ['no effective policies attached'],
      }
    },
  }
}

export function policiesClientFromEnv(mode: string | undefined = import.meta.env.VITE_API_MODE): PoliciesClient {
  return mode === 'fixture' ? createPoliciesFixtureClient() : createPoliciesHttpClient('/api/policies')
}
