/** Mode label for audit entries and agent steps. mock and fixture are always labelled SIMULATED. */
export function ModeChip({ mode }: { mode: string | null | undefined }) {
  if (!mode) return <span className="badge mode-none" title="No money mode recorded for this entry">NO MODE</span>
  if (mode === 'stripe_test') return <span className="badge mode-stripe_test" title="Stripe test mode object (livemode false)">STRIPE TEST</span>
  if (mode === 'mock' || mode === 'fixture')
    return <span className={`badge mode-${mode}`} title="Simulated: no Stripe call was made">{mode.toUpperCase()} · SIMULATED</span>
  return <span className="badge mode-none">{mode.toUpperCase()}</span>
}

const VERDICTS = ['allow', 'hold', 'deny', 'approval_required']
export function VerdictChip({ verdict }: { verdict: string | null | undefined }) {
  if (!verdict) return null
  const cls = VERDICTS.includes(verdict) ? `v-${verdict}` : ''
  return <span className={`vchip ${cls}`}>{verdict.replace('_', ' ')}</span>
}
