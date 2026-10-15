import { arr, defaultFetch, jsonInit, num, pick, readWire, rec, str } from '../lib/wire'
import type { FetchImpl } from '../lib/wire'
import type { Explanation, WhyClause, WhyClient } from './types'

const show = (v: unknown): string | null => (v === undefined || v === null ? null : typeof v === 'string' ? v : JSON.stringify(v))

function clause(raw: unknown): WhyClause {
  const o = rec(raw)
  const outcome = str(o.outcome) ?? null
  const passed = typeof o.passed === 'boolean' ? o.passed : outcome ? outcome === 'allow' : null
  return {
    policyId: str(pick(o, 'policyId', 'policy_id')) ?? null,
    version: num(pick(o, 'version', 'policyVersion', 'policy_version')) ?? null,
    clauseId: str(pick(o, 'clauseId', 'clause_id', 'clause', 'id')) ?? '?',
    prose: str(o.prose) ?? null,
    passed,
    outcome,
    observed: show(o.observed),
    expected: show(o.expected),
    evidence: pick(o, 'evidence', 'evidenceIds', 'evidence_ids'),
  }
}

/** Tolerant of snake/camel; unknown source is reported as unknown rather than assumed to be Cognee. */
export function normalizeExplanation(raw: unknown, paymentId: string): Explanation {
  const o = rec(raw)
  const pol = rec(o.policy)
  const stripe = rec(o.stripe)
  const src = str(o.source)
  const ids = [
    ...arr(pick(stripe, 'ids')), ...arr(pick(o, 'stripeIds', 'stripe_ids', 'providerObjectIds')),
    ...(str(pick(o, 'providerObjectId', 'provider_object_id')) ? [pick(o, 'providerObjectId', 'provider_object_id')] : []),
  ].filter((x): x is string => typeof x === 'string')
  const ev = pick(o, 'evidence')
  const evidence = Array.isArray(ev)
    ? ev.map((x, i) => (typeof x === 'string' ? { label: `evidence ${i + 1}`, value: x } : { label: str(rec(x).label ?? rec(x).kind ?? rec(x).id) ?? `evidence ${i + 1}`, value: show(x) ?? '' }))
    : Object.entries(rec(ev)).map(([k, v]) => ({ label: k, value: show(v) ?? '' }))
  return {
    paymentId: str(pick(o, 'paymentId', 'payment_id', 'operationId')) ?? paymentId,
    verdict: str(o.verdict) ?? str(pol.verdict) ?? null,
    policy: {
      id: str(pick(pol, 'id', 'policyId', 'policy_id')) ?? str(pick(o, 'policyId', 'policy_id')) ?? null,
      version: num(pick(pol, 'version')) ?? num(pick(o, 'policyVersion', 'policy_version')) ?? null,
      hash: str(pick(pol, 'hash', 'contentHash')) ?? str(pick(o, 'effectivePolicyHash', 'effective_policy_hash', 'policyHash', 'policy_hash')) ?? null,
    },
    clauses: arr(pick(o, 'clauses', 'results')).map(clause),
    evidence,
    stripeIds: [...new Set(ids)],
    livemode: typeof stripe.livemode === 'boolean' ? stripe.livemode : typeof o.livemode === 'boolean' ? o.livemode : null,
    mode: str(o.mode) ?? null,
    source: src === 'cognee' || src === 'audit_log' ? src : 'unknown',
    sourceNote: str(pick(o, 'sourceNote', 'source_note', 'memoryStatus', 'memory_status', 'fallbackReason', 'fallback_reason')) ?? null,
    text: str(pick(o, 'text', 'explanation', 'summary')) ?? null,
    auditSeqs: arr(pick(o, 'auditSeqs', 'audit_seqs', 'auditEntries', 'audit_entries')).map((x) => num(x) ?? num(rec(x).seq)).filter((x): x is number => x !== undefined),
  }
}

export function createWhyHttpClient(base = '', fetchImpl: FetchImpl = defaultFetch): WhyClient {
  return {
    kind: 'http',
    explain: (id) =>
      fetchImpl(`${base}/api/payments/${encodeURIComponent(id)}/explanation`, { ...jsonInit('GET'), headers: { accept: 'application/json', 'X-Party-Role': 'admin', 'X-Party-Id': 'console' } })
        .then((r) => readWire<unknown>(r))
        .then((raw) => normalizeExplanation(raw, id)),
  }
}

export function createWhyFixtureClient(): WhyClient {
  return {
    kind: 'fixture',
    async explain(id) {
      return normalizeExplanation({
        verdict: 'allow', mode: 'fixture', source: 'audit_log',
        sourceNote: 'Fixture data. No Cognee retrieval happened.',
        policy: { id: 'org-base', version: 1, hash: 'fixture0001' },
        clauses: [
          { clause: 'PAY-1', prose: 'Release only after delivery proof', outcome: 'allow', evidence: { receipt: 'confirmed' } },
          { clause: 'APPROVE-1', prose: 'Amounts above 50.00 need approval', outcome: 'allow', evidence: { amount_minor: 4200, limit_minor: 5000 } },
        ],
        evidence: { receipt: 'confirmed by receiver' },
        auditSeqs: [5, 6],
      }, id)
    },
  }
}

export function whyClientFromEnv(mode: string | undefined = import.meta.env.VITE_API_MODE): WhyClient {
  return mode === 'fixture' ? createWhyFixtureClient() : createWhyHttpClient()
}
