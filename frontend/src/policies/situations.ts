import type { DecideInput } from './types'

export interface Situation {
  id: string
  label: string
  amountMinor: number
  inputs: Record<string, unknown>
}

const OK_INPUTS = { receipt_confirmed: true, prohibited_zone: false, currency: 'usd', insured: true }

/** Made-up situations for the preview strip. They only feed the decide endpoint; nothing is paid. */
export const SITUATIONS: Situation[] = [
  { id: 'ok', label: 'All in order', amountMinor: 4200, inputs: OK_INPUTS },
  { id: 'receipt', label: 'Receipt missing', amountMinor: 4200, inputs: { ...OK_INPUTS, receipt_confirmed: false } },
  { id: 'zone', label: 'Prohibited zone', amountMinor: 4200, inputs: { ...OK_INPUTS, prohibited_zone: true } },
  { id: 'limit', label: 'Over limit', amountMinor: 9000, inputs: OK_INPUTS },
  { id: 'currency', label: 'Wrong currency', amountMinor: 4200, inputs: { ...OK_INPUTS, currency: 'eur' } },
  { id: 'payee', label: 'Unlisted payee', amountMinor: 4200, inputs: { ...OK_INPUTS, recipient: 'Unknown Co', merchant_category: 'gambling' } },
]

export function situationInput(s: Situation, target: DecideInput['target'], inherited: DecideInput['inherited']): DecideInput {
  return { target, inherited, inputs: s.inputs, inputRevision: 1, amountMinor: s.amountMinor }
}
