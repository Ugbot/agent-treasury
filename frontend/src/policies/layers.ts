import type { Attachment, PolicyBundle, TargetRef, TargetType } from './types'

export interface LayerDef {
  type: TargetType
  title: string
  blurb: string
}

/** Resolution order from PolicyRegistry.effective_attachments: organisation, job, card or counterparty, this payment. */
export const LAYERS: LayerDef[] = [
  { type: 'organisation', title: 'Organisation rules', blurb: 'Apply to every payment. Locked, nobody can switch them off.' },
  { type: 'mission', title: 'Job policies', blurb: 'Added for this delivery job.' },
  { type: 'card', title: 'Card and counterparty', blurb: 'Added for the card that pays and who gets paid.' },
  { type: 'payment', title: 'This payment', blurb: 'Added for this one payment only.' },
]

export interface LayerPolicy {
  attachment: Attachment
  bundle: PolicyBundle | undefined
}
export interface Layer extends LayerDef {
  policies: LayerPolicy[]
  adds: number
  inForce: number
}

/** Group attachments by layer, in resolution order, with a running count of rules in force. */
export function buildLayers(attachments: Attachment[], bundles: PolicyBundle[], chain: TargetRef[]): Layer[] {
  let running = 0
  return LAYERS.map((def) => {
    const target = chain.find((t) => t.type === def.type)
    const policies = attachments
      .filter((a) => a.target_type === def.type && (!target || a.target_id === target.id))
      .map((attachment) => ({
        attachment,
        bundle: bundles.find((b) => b.policy_id === attachment.policy_id && b.version === attachment.policy_version),
      }))
    const adds = policies.reduce((n, p) => n + (p.bundle?.clauses.length ?? 0), 0)
    running += adds
    return { ...def, policies, adds, inForce: running }
  })
}
