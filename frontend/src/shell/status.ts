import { formatMoney } from '../lib/wire'
import type { ClauseResult, Snapshot } from '../types'

export type Tone = 'idle' | 'allow' | 'hold' | 'deny'

export interface Status {
  tone: Tone
  /** Short label for the state, e.g. "Held". */
  label: string
  /** One plain-English sentence (or two) that answers what is happening and why. */
  text: string
}

/** Plain-English reasons for the known rules. Unknown rules fall back to their own sentence. */
const REASON: Record<string, string> = {
  'PAY-1': 'waiting for the customer to confirm receipt',
  'APPROVE-1': 'the amount is above the approval limit, so a person must approve it',
  'TRAVEL-1': 'the route enters a prohibited zone',
  'CURRENCY-1': 'the currency is not one the organisation allows',
}

export function reasonFor(c: ClauseResult): string {
  return REASON[c.clauseId] ?? (c.prose ? c.prose.charAt(0).toLowerCase() + c.prose.slice(1) : 'a rule did not pass')
}

/** Turns the raw observed value into something a person can read. */
export function plainObserved(observed: string): string {
  try {
    const o = JSON.parse(observed) as unknown
    if (o && typeof o === 'object') {
      const miss = (o as Record<string, unknown>).missing
      if (typeof miss === 'string') return `missing ${miss.replace(/_/g, ' ')}`
      return Object.entries(o as Record<string, unknown>).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${String(v)}`).join(', ')
    }
  } catch {
    /* not JSON */
  }
  return observed
}

const ruleSuffix = (failed: ClauseResult[]) =>
  failed.length === 0 ? '' : failed.length === 1 ? ` (rule ${failed[0].clauseId})` : ` (rule ${failed[0].clauseId} and ${failed.length - 1} more)`

export function describeStatus(s: Snapshot | null): Status {
  if (!s) return { tone: 'idle', label: 'Loading', text: 'Loading the current mission. If this does not finish, the backend is not reachable.' }
  const d = s.decision
  const amount = formatMoney(s.inputs.amountMinor, s.inputs.currency)
  if (!d) return { tone: 'idle', label: 'Waiting', text: `No payment decision yet. Run a scenario on the left, or change an input below, to start. ${amount} is the amount in play.` }
  const failed = d.clauses.filter((c) => !c.passed)
  const reason = failed.length ? reasonFor(failed[0]) : ''
  const op = s.operation
  switch (d.verdict) {
    case 'allow':
      return op
        ? { tone: 'allow', label: 'Paid', text: `Paid: ${formatMoney(op.amountMinor, op.currency)} released to the merchant and driver. Every rule passed.` }
        : { tone: 'allow', label: 'Allowed', text: `Allowed: every rule passed. ${amount} will be paid to the merchant and driver.` }
    case 'hold':
      return { tone: 'hold', label: 'Held', text: `Held: ${reason}${ruleSuffix(failed)}. ${amount} will be paid to the merchant and driver once it passes.` }
    case 'approval_required':
      return { tone: 'hold', label: 'Needs approval', text: `Needs approval: ${reason}${ruleSuffix(failed)}. ${amount} will be paid once a person approves it.` }
    case 'deny':
      return { tone: 'deny', label: 'Denied', text: `Denied: ${reason}${ruleSuffix(failed)}. No money moves.` }
  }
}

/* ---------- pipeline model ---------- */

export type StageState = 'idle' | 'ok' | 'wait' | 'stop'

export interface InputRow {
  key: string
  label: string
  value: string
  met: boolean
  /** Receipt, insurance and location are simulated in this demo. */
  simulated: boolean
  note?: string
}

export function inputRows(s: Snapshot): InputRow[] {
  const clause = (id: string) => s.decision?.clauses.find((c) => c.clauseId === id)
  const approve = clause('APPROVE-1')
  const travel = clause('TRAVEL-1')
  return [
    { key: 'amount', label: 'Amount', value: formatMoney(s.inputs.amountMinor, s.inputs.currency), met: approve ? approve.passed : true, simulated: false, note: approve && !approve.passed ? 'above the approval limit' : undefined },
    { key: 'insurance', label: 'Insurance', value: s.inputs.insured ? 'covered' : 'not covered', met: s.inputs.insured, simulated: true },
    { key: 'location', label: 'Location', value: s.inputs.location, met: travel ? travel.passed : s.inputs.location !== 'prohibited', simulated: true, note: travel && !travel.passed ? 'prohibited zone' : undefined },
    { key: 'receipt', label: 'Receipt', value: s.inputs.receiptConfirmed ? 'confirmed' : 'not confirmed', met: s.inputs.receiptConfirmed, simulated: true },
  ]
}

export interface PipelineStates {
  inputs: StageState
  policies: StageState
  decision: StageState
  money: StageState
}

export function pipelineStates(s: Snapshot | null): PipelineStates {
  if (!s) return { inputs: 'idle', policies: 'idle', decision: 'idle', money: 'idle' }
  const d = s.decision
  const inputs: StageState = inputRows(s).every((r) => r.met) ? 'ok' : s.decision?.verdict === 'deny' ? 'stop' : 'wait'
  if (!d) return { inputs, policies: 'idle', decision: 'idle', money: 'idle' }
  const anyFail = d.clauses.some((c) => !c.passed)
  const decision: StageState = d.verdict === 'allow' ? 'ok' : d.verdict === 'deny' ? 'stop' : 'wait'
  const money: StageState = s.operation ? 'ok' : d.verdict === 'deny' ? 'stop' : d.verdict === 'allow' ? 'wait' : 'wait'
  return { inputs, policies: anyFail ? (d.verdict === 'deny' ? 'stop' : 'wait') : 'ok', decision, money }
}

export const POLICY_NAME: Record<string, string> = {
  'org-base': 'Organisation base policy',
  'travel-insured': 'Insured travel only',
}
export const policyName = (id: string) => POLICY_NAME[id] ?? id
