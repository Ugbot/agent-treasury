/** View model for money flows. Money is integer minor units; the backend owns authority. */
export type FlowKind = 'invoice' | 'collect' | 'transfer' | 'card_create' | 'fuel_authorization' | 'fuel_capture' | 'payout' | 'refund'
export type FlowStatus = 'planned' | 'reserved' | 'pending' | 'confirmed' | 'partially_settled' | 'unknown' | 'denied' | 'held' | 'compensation_required' | 'failed'
export type FlowVerdict = 'allow' | 'hold' | 'deny'
export type MoneyMode = 'fixture' | 'mock' | 'stripe_test'
export type Party = 'customer' | 'platform' | 'merchant' | 'driver' | 'fuel_card' | 'bank'

export interface Evidence {
  policy: { policyId: string; version: number; hash: string } | null
  clauses: { clauseId: string; version: number; passed: boolean; prose: string }[]
  inputSnapshot: Record<string, string | number | boolean>
  approvals: string[]
  receipt: { objectType: string; objectId: string; status: string } | null
  lineage: { planId: string; sagaSteps: string[] }
}

export interface Flow {
  id: string
  kind: FlowKind
  from: Party
  to: Party
  status: FlowStatus
  verdict: FlowVerdict
  mode: MoneyMode
  livemode: boolean
  grossMinor: number
  retentionMinor: number
  feeMinor: number
  settledMinor: number
  providerObjectId: string | null
  attachments: string[]
  evidence: Evidence
}

export interface Allowance { id: string; label: string; limitMinor: number; reservedMinor: number; capturedMinor: number }
export interface Funds { pendingMinor: number; availableMinor: number; inTransitToBankMinor: number }
export interface SagaStep { stepId: string; name: string; status: 'pending' | 'running' | 'done' | 'failed' | 'compensated'; operationId: string | null; evidenceIds: string[]; mode: MoneyMode; providerObjectId: string | null }
export interface MoneyData { missionId: string; flows: Flow[]; allowances: Allowance[]; funds: Funds; saga: SagaStep[] }

export const netMinor = (f: Flow) => f.grossMinor - f.retentionMinor - f.feeMinor

export const gbp = (minor: number) =>
  new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP' }).format(minor / 100)

const SECRET = /\b(?:sk|rk|pk)_(?:test|live)_[A-Za-z0-9]+|whsec_[A-Za-z0-9]+/g
/** Redact anything key-shaped before display. */
export const redact = (s: string) => s.replace(SECRET, '[redacted]')

export const KIND_LABEL: Record<FlowKind, string> = {
  invoice: 'Invoice', collect: 'Customer collection', transfer: 'Transfer', card_create: 'Card created',
  fuel_authorization: 'Fuel authorization', fuel_capture: 'Fuel capture', payout: 'Bank payout', refund: 'Refund',
}

/** Honest status wording. An invoice that is not confirmed is never "paid". */
export function statusLabel(f: Flow): string {
  if (f.livemode) return 'REJECTED: live-mode object'
  const done = f.status === 'confirmed'
  switch (f.kind) {
    case 'invoice': return done ? 'Invoice paid' : 'Invoice issued, unpaid'
    case 'collect': return done ? 'Collected (pending funds until available)' : `Collection ${f.status.replace(/_/g, ' ')}`
    case 'card_create': return done ? 'Card created, no spend yet' : `Card ${f.status.replace(/_/g, ' ')}`
    case 'fuel_authorization': return done ? 'Authorized, not captured' : `Authorization ${f.status.replace(/_/g, ' ')}`
    case 'fuel_capture': return done ? 'Captured' : `Capture ${f.status.replace(/_/g, ' ')}`
    case 'transfer': return done ? 'Transferred to connected account' : `Transfer ${f.status.replace(/_/g, ' ')}`
    case 'payout': return done ? 'Paid out to bank' : `Payout ${f.status.replace(/_/g, ' ')}`
    case 'refund': return done ? 'Refunded' : `Refund ${f.status.replace(/_/g, ' ')}`
  }
}

export function flowClass(f: Flow): string {
  if (f.livemode) return 'denied'
  if (f.status === 'compensation_required') return 'compensation'
  if (f.status === 'partially_settled') return 'partial'
  if (f.verdict === 'deny' || f.status === 'denied') return 'denied'
  if (f.verdict === 'hold' || f.status === 'held') return 'held'
  return 'allowed'
}

export const flowSymbol = (c: string) => ({ allowed: '✓', held: '⏸', denied: '✕', partial: '◐', compensation: '↺' }[c] ?? '?')

export function fixtureMoney(missionId = 'fixture'): MoneyData {
  const ev = (clause: boolean, receipt: Evidence['receipt'], approvals: string[] = []): Evidence => ({
    policy: { policyId: 'travel-insured', version: 2, hash: 'fixture0001' },
    clauses: [{ clauseId: 'PAY-1', version: 1, passed: clause, prose: 'Release only after delivery proof' }],
    inputSnapshot: { amountMinor: 4200, insured: true, receiptConfirmed: clause },
    approvals, receipt, lineage: { planId: 'plan-1', sagaSteps: ['s1', 's2'] },
  })
  const base = { mode: 'fixture' as const, livemode: false, verdict: 'allow' as const, attachments: ['org-base v1'] }
  return {
    missionId,
    flows: [
      { ...base, id: 'f1', kind: 'invoice', from: 'platform', to: 'customer', status: 'pending', grossMinor: 10000, retentionMinor: 0, feeMinor: 0, settledMinor: 0, providerObjectId: 'in_fixture1', evidence: ev(false, { objectType: 'invoice', objectId: 'in_fixture1', status: 'open' }) },
      { ...base, id: 'f2', kind: 'collect', from: 'customer', to: 'platform', status: 'confirmed', grossMinor: 10000, retentionMinor: 1000, feeMinor: 320, settledMinor: 10000, providerObjectId: 'pi_fixture1', evidence: ev(true, { objectType: 'payment_intent', objectId: 'pi_fixture1', status: 'succeeded' }) },
      { ...base, id: 'f3', kind: 'transfer', from: 'platform', to: 'merchant', status: 'partially_settled', grossMinor: 6000, retentionMinor: 0, feeMinor: 0, settledMinor: 4000, providerObjectId: 'tr_fixture1', evidence: ev(true, { objectType: 'transfer', objectId: 'tr_fixture1', status: 'paid' }, ['APPROVE-1']) },
      { ...base, id: 'f4', kind: 'transfer', from: 'platform', to: 'driver', status: 'held', verdict: 'hold', grossMinor: 2680, retentionMinor: 0, feeMinor: 0, settledMinor: 0, providerObjectId: null, evidence: ev(false, null) },
      { ...base, id: 'f5', kind: 'fuel_authorization', from: 'platform', to: 'fuel_card', status: 'confirmed', grossMinor: 4200, retentionMinor: 0, feeMinor: 0, settledMinor: 0, providerObjectId: 'iauth_fixture1', evidence: ev(true, { objectType: 'issuing.authorization', objectId: 'iauth_fixture1', status: 'pending' }) },
      { ...base, id: 'f6', kind: 'refund', from: 'platform', to: 'customer', status: 'compensation_required', grossMinor: 500, retentionMinor: 0, feeMinor: 0, settledMinor: 0, providerObjectId: null, evidence: ev(false, null) },
      { ...base, id: 'f7', kind: 'payout', from: 'platform', to: 'bank', status: 'unknown', grossMinor: 3000, retentionMinor: 0, feeMinor: 0, settledMinor: 0, providerObjectId: 'po_fixture1', evidence: ev(true, { objectType: 'payout', objectId: 'po_fixture1', status: 'unknown' }) },
    ],
    allowances: [
      { id: 'a1', label: 'Fuel card allowance', limitMinor: 6000, reservedMinor: 4200, capturedMinor: 0 },
      { id: 'a2', label: 'Mission budget', limitMinor: 20000, reservedMinor: 6000, capturedMinor: 10000 },
    ],
    funds: { pendingMinor: 6000, availableMinor: 2680, inTransitToBankMinor: 0 },
    saga: [
      { stepId: 's1', name: 'Collect from customer', status: 'done', operationId: 'f2', evidenceIds: ['ev1'], mode: 'fixture', providerObjectId: 'pi_fixture1' },
      { stepId: 's2', name: 'Transfer to merchant', status: 'running', operationId: 'f3', evidenceIds: ['ev2'], mode: 'fixture', providerObjectId: 'tr_fixture1' },
      { stepId: 's3', name: 'Refund compensation', status: 'pending', operationId: 'f6', evidenceIds: [], mode: 'fixture', providerObjectId: null },
    ],
  }
}
