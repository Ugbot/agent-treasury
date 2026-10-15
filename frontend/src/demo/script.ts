export type DemoAction =
  | { kind: 'view'; view: 'policies' | 'mission' }
  | { kind: 'run'; scenario: string }
  | { kind: 'freeze' }
  | { kind: 'why' }

export interface DemoStep {
  id: string
  caption: string
  seconds: number
  action: DemoAction
  optional?: boolean
}

/** The two-minute story, in order. Per-step durations are the single source of timing truth. */
export const DEMO_STEPS: DemoStep[] = [
  { id: 'policies', seconds: 20, action: { kind: 'view', view: 'policies' },
    caption: 'Written policies chain together: change an input and a hold flips to allow.' },
  { id: 'deliver', seconds: 25, action: { kind: 'run', scenario: 'deliver_and_release' },
    caption: 'Delivery confirmed, so the policy allows it and the money moves with real Stripe ids.' },
  { id: 'replan', seconds: 20, action: { kind: 'run', scenario: 'coverage_replan' },
    caption: 'Coverage changed, so the agent replans and the policy checks the new plan.' },
  { id: 'refund', seconds: 20, action: { kind: 'run', scenario: 'cancel_refund' },
    caption: 'The job is cancelled before pickup, so the payment is refunded.' },
  { id: 'freeze', seconds: 10, action: { kind: 'freeze' }, optional: true,
    caption: 'One approver switch freezes every payment instantly.' },
  { id: 'why', seconds: 15, action: { kind: 'why' },
    caption: 'Why was this paid? Every payment cites the exact clause that allowed it.' },
]

export const MAX_TOTAL_SECONDS = 110

export const totalSeconds = (steps: DemoStep[] = DEMO_STEPS) => steps.reduce((n, s) => n + s.seconds, 0)

/** Drop optional steps whose backing feature is missing, keeping order. */
export function planSteps(freezeAvailable: boolean, steps: DemoStep[] = DEMO_STEPS): DemoStep[] {
  return steps.filter((s) => !(s.optional && s.action.kind === 'freeze' && !freezeAvailable))
}

/** Which step is current after `elapsed` seconds, and how far into it. Null when finished. */
export function stepAt(steps: DemoStep[], elapsed: number): { index: number; into: number } | null {
  let t = 0
  for (let i = 0; i < steps.length; i++) {
    if (elapsed < t + steps[i].seconds) return { index: i, into: elapsed - t }
    t += steps[i].seconds
  }
  return null
}

export const stepStart = (steps: DemoStep[], index: number) => steps.slice(0, index).reduce((n, s) => n + s.seconds, 0)

export const freshRunId = (now = Date.now(), rnd = Math.random()) => `demo-${now.toString(36)}-${Math.floor(rnd * 1e6).toString(36)}`
