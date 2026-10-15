import type { ClauseOutcome, Outcome, WireClause } from './types'

/** Plain-English rendering of typed clause data. Never shows raw params; unknown shapes fall back to the written prose. */

export function money(minor: number, currency = 'USD'): string {
  const v = (minor / 100).toFixed(2)
  return currency.toUpperCase() === 'USD' ? `$${v}` : `${currency.toUpperCase()} ${v}`
}

const KNOWN_INPUT: Record<string, { need: string; forbid: string }> = {
  receipt_confirmed: { need: 'Release only after the customer confirms receipt', forbid: 'Deny if the customer has confirmed receipt' },
  prohibited_zone: { need: 'Hold until the route is outside every prohibited zone', forbid: 'Deny if the route enters a prohibited zone' },
  insured: { need: 'Hold until the driver is insured', forbid: 'Deny if the driver is insured' },
}

export function humanKey(key: string): string {
  return key.replace(/_/g, ' ').trim() || 'the required input'
}

const upper = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

function currencyList(cs: unknown): string[] {
  return Array.isArray(cs) ? cs.map((c) => String(c).toUpperCase()) : []
}

function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.map((x) => String(x).trim()).filter(Boolean) : []
}

export function windowText(sec: number): string {
  if (sec % 3600 === 0) { const h = sec / 3600; return h === 1 ? 'hour' : `${h} hours` }
  if (sec % 60 === 0) { const m = sec / 60; return m === 1 ? 'minute' : `${m} minutes` }
  return sec === 1 ? 'second' : `${sec} seconds`
}

export function joinOr(items: string[]): string {
  if (items.length <= 1) return items[0] ?? ''
  return `${items.slice(0, -1).join(', ')} or ${items[items.length - 1]}`
}

/** One sentence a finance person can read without knowing the schema. */
export function clauseSentence(c: WireClause): string {
  const p = c.params ?? {}
  switch (c.kind) {
    case 'max_amount': {
      const limit = Number(p.limit_minor)
      if (!Number.isFinite(limit)) return c.prose
      return `Hold any payment over ${money(limit)} until a person approves it`
    }
    case 'require_input': {
      const key = String(p.key ?? '')
      if (!key) return c.prose
      return KNOWN_INPUT[key]?.need ?? `Hold until ${humanKey(key)} is confirmed`
    }
    case 'forbid_input': {
      const key = String(p.key ?? '')
      if (!key) return c.prose
      return KNOWN_INPUT[key]?.forbid ?? `Deny if ${humanKey(key)} is present`
    }
    case 'allowed_currency': {
      const cs = currencyList(p.currencies)
      return cs.length ? `Only settle in ${joinOr(cs)}` : c.prose
    }
    case 'allowed_counterparties': {
      const r = strList(p.recipients), cat = strList(p.categories)
      if (!r.length && !cat.length) return c.prose
      const parts = [r.length ? `Only pay these payees: ${r.join(', ')}` : '', cat.length ? `${r.length ? 'and only' : 'Only pay'} these categories: ${cat.join(', ')}` : '']
      return parts.filter(Boolean).join('; ')
    }
    case 'velocity_limit': {
      const n = Number(p.max_payments), w = Number(p.window_seconds)
      if (!(n >= 1) || !(w >= 1)) return c.prose
      return `No more than ${n} ${n === 1 ? 'payment' : 'payments'} every ${windowText(w)}`
    }
    case 'payment_freeze':
      return p.active ? 'Payments are frozen' : 'Payments are not frozen'
    default:
      return c.prose
  }
}

/** The worst verdict this clause can produce when it is not met. */
export function clauseVerdict(c: WireClause): Outcome {
  return c.kind === 'forbid_input' || c.kind === 'allowed_currency' || c.kind === 'allowed_counterparties' ? 'deny' : 'hold'
}

export const VERDICT_LABEL: Record<Outcome, string> = { allow: 'Allow', hold: 'Hold', deny: 'Deny' }

/** One line saying why a clause produced its outcome, from the evidence the decision recorded. */
export function evidenceSentence(c: WireClause | undefined, r: ClauseOutcome): string {
  const ev = r.evidence ?? {}
  if (r.outcome === 'allow') return 'Met'
  if ('amount_minor' in ev && 'limit_minor' in ev)
    return `Amount ${money(Number(ev.amount_minor))} is over the ${money(Number(ev.limit_minor))} limit`
  if ('currency' in ev && 'allowed' in ev) return `${String(ev.currency).toUpperCase()} is not an allowed currency`
  if (ev.missing === 'currency') return 'No currency was supplied'
  if (typeof ev.missing === 'string') return `Still waiting on: ${humanKey(ev.missing)}`
  if (typeof ev.present === 'string') return `Found: ${humanKey(ev.present)}`
  return c ? upper(clauseSentence(c)) : 'Rule not met'
}

export function summarise(clauses: WireClause[]): string {
  if (!clauses.length) return 'No rules'
  const first = clauseSentence(clauses[0])
  return clauses.length === 1 ? first : `${first}, and ${clauses.length - 1} more`
}
