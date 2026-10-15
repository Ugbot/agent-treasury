import type { Command, Scope } from '../types'

export interface Ctx {
  missionId: string
  expectedRevision: number
  /** Policy currency (CURRENCY-1) from the snapshot; never assumed by the UI. */
  currency?: string
}

export function newCommandId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `cmd-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function env<K extends Command['type']>(type: K, ctx: Ctx, payload: Extract<Command, { type: K }>['payload']) {
  return {
    commandId: newCommandId(),
    type,
    missionId: ctx.missionId,
    expectedRevision: ctx.expectedRevision,
    issuedAt: new Date().toISOString(),
    payload,
  } as Extract<Command, { type: K }>
}

export const setAmount = (ctx: Ctx, amountMinor: number) => env('set_amount', ctx, { amountMinor, currency: ctx.currency ?? 'usd' })
export const setInsurance = (ctx: Ctx, covered: boolean) => env('set_insurance', ctx, { covered })
export const setLocation = (ctx: Ctx, location: string) => env('set_location', ctx, { location })
export const confirmReceipt = (ctx: Ctx, recipientId = 'customer', receiptNonce = newCommandId()) =>
  env('confirm_receipt', ctx, { recipientId, receiptNonce })
export const attachPolicy = (ctx: Ctx, policyId: string, policyVersion: number, targetType: Scope = 'payment', targetId = 'current') =>
  env('attach_policy', ctx, { targetType, targetId, policyId, policyVersion })
export const selectScenario = (ctx: Ctx, scenario: string, mode: 'fixture' | 'mock' | 'stripe_test') =>
  env('select_scenario', ctx, { scenario, mode })
export const reportPosition = (ctx: Ctx, driverId: string, x: number, y: number, assignmentVersion = 0) =>
  env('report_position', ctx, { driverId, x, y, assignmentVersion })
export const cancelRequested = (ctx: Ctx, reason: string) => env('cancel_requested', ctx, { reason })
