import { clauseSentence, clauseVerdict, humanKey } from '../policies/clauses'
import type { WireClause } from './types'

export const KIND_OPTIONS: { kind: string; label: string; help: string }[] = [
  { kind: 'max_amount', label: 'Limit the amount', help: 'Hold payments over a dollar limit for a person to approve.' },
  { kind: 'require_input', label: 'Require something first', help: 'Hold the payment until something is confirmed.' },
  { kind: 'forbid_input', label: 'Forbid something', help: 'Deny the payment when something is true.' },
  { kind: 'allowed_currency', label: 'Allowed currencies', help: 'Deny payments in any other currency.' },
  { kind: 'allowed_counterparties', label: 'Allowed payees', help: 'Deny payments to anyone not on the list; hold when the payee is unknown.' },
  { kind: 'velocity_limit', label: 'Payment rate limit', help: 'Hold payments once too many have gone out in a time window.' },
  { kind: 'payment_freeze', label: 'Payment freeze', help: 'Hold every payment. Only an approver can lift it, from the app bar.' },
]
export const kindLabel = (k: string) => KIND_OPTIONS.find((o) => o.kind === k)?.label ?? k

export const INPUT_KEYS: { key: string; label: string }[] = [
  { key: 'receipt_confirmed', label: 'Receipt confirmed' },
  { key: 'prohibited_zone', label: 'Route enters a prohibited zone' },
  { key: 'insured', label: 'Driver is insured' },
]
export const inputKeyLabel = (k: string) => INPUT_KEYS.find((i) => i.key === k)?.label ?? humanKey(k)
export const CURRENCIES = ['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY']

/** Dollars typed by a person to integer minor units; null when it is not a positive amount with at most 2 decimals. */
export function dollarsToMinor(s: string): number | null {
  const t = s.trim().replace(/^\$/, '')
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return null
  const n = Math.round(parseFloat(t) * 100)
  return n > 0 ? n : null
}
export const minorToDollars = (m: number): string => (m / 100).toFixed(2)

export function defaultClause(kind: string, id: string): WireClause {
  const params: Record<string, unknown> =
    kind === 'max_amount' ? { limit_minor: 5000, currency: 'USD' }
    : kind === 'allowed_counterparties' ? { recipients: [], categories: [] }
    : kind === 'velocity_limit' ? { max_payments: 3, window_seconds: 600 }
    : kind === 'payment_freeze' ? { active: true }
    : kind === 'allowed_currency' ? { currencies: ['USD'] }
    : { key: kind === 'forbid_input' ? 'prohibited_zone' : 'receipt_confirmed' }
  const c = { id, prose: '', kind, params }
  return { ...c, prose: clauseSentence(c) }
}

/** The sentence a person reads; recomputed from the typed fields so it never lags an edit. */
export const sentenceFor = (c: WireClause): string => clauseSentence(c)
export const verdictFor = clauseVerdict

export function nextClauseId(clauses: { id: string }[]): string {
  let n = clauses.length + 1
  while (clauses.some((c) => c.id === `R-${n}`)) n++
  return `R-${n}`
}

/** Comma separated text to a clean list. */
export const parseList = (s: string): string[] => s.split(',').map((x) => x.trim()).filter(Boolean)
