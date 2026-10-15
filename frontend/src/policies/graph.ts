import type { Attachment, ClauseOutcome, DecisionOut, PolicyBundle, TargetType } from './types'

export const SCOPE_ORDER: TargetType[] = ['organisation', 'mission', 'card', 'payment']

export interface GraphNode {
  id: string
  label: string
  sub: string
  x: number
  y: number
  kind: 'policy' | 'decision'
  attachment?: Attachment
}
export interface GraphEdge {
  from: string
  to: string
}
export interface GraphLayout {
  nodes: GraphNode[]
  edges: GraphEdge[]
  width: number
  height: number
}

export const NODE_W = 150
export const NODE_H = 46
const COL_GAP = 60
const ROW_GAP = 16
const PAD = 16

/** Column per scope that has attachments (org -> mission -> card -> payment), then a decision node. Each column chains into the next. */
export function layoutChain(attachments: Attachment[]): GraphLayout {
  const cols = SCOPE_ORDER.map((s) => attachments.filter((a) => a.target_type === s)).filter((c) => c.length > 0)
  const nodes: GraphNode[] = []
  const edges: GraphEdge[] = []
  let tallest = 1
  cols.forEach((col, ci) => {
    tallest = Math.max(tallest, col.length)
    col.forEach((a, ri) => {
      nodes.push({
        id: a.attachment_id,
        label: `${a.policy_id} v${a.policy_version}`,
        sub: `${a.target_type}${a.mandatory ? ' (mandatory)' : ''}`,
        x: PAD + ci * (NODE_W + COL_GAP),
        y: PAD + ri * (NODE_H + ROW_GAP),
        kind: 'policy',
        attachment: a,
      })
    })
  })
  const height = PAD * 2 + tallest * NODE_H + (tallest - 1) * ROW_GAP
  const dx = PAD + cols.length * (NODE_W + COL_GAP)
  nodes.push({ id: 'decision', label: 'Decision', sub: 'deny > hold > allow', x: dx, y: PAD + (height - PAD * 2 - NODE_H) / 2, kind: 'decision' })
  cols.forEach((col, ci) => {
    const next = ci + 1 < cols.length ? cols[ci + 1].map((a) => a.attachment_id) : ['decision']
    for (const a of col) for (const t of next) edges.push({ from: a.attachment_id, to: t })
  })
  return { nodes, edges, width: dx + NODE_W + PAD, height }
}

/** Effective rule lines for one attached policy, in plain words. */
export function ruleText(b: PolicyBundle): string[] {
  return b.clauses.map((c) => {
    const key = String(c.params.key ?? '')
    switch (c.kind) {
      case 'max_amount':
        return `${c.id}: hold if amount exceeds ${fmtMinor(Number(c.params.limit_minor))}`
      case 'require_input':
        return `${c.id}: hold unless ${key} is true`
      case 'forbid_input':
        return `${c.id}: deny if ${key} is true`
      default:
        return `${c.id}: ${c.prose}`
    }
  })
}

export function fmtMinor(minor: number): string {
  return `USD ${(minor / 100).toFixed(2)}`
}

export interface Explanation {
  headline: string
  held: string[]
  denied: string[]
  passed: string[]
}

function line(r: ClauseOutcome): string {
  const ev = Object.entries(r.evidence).map(([k, v]) => `${k}=${String(v)}`).join(', ')
  return `${r.policy_id}${r.policy_version ? ` v${r.policy_version}` : ''} ${r.clause ?? '(policy)'}${ev ? ` [${ev}]` : ''}`
}

/** Why-held / why-paid from a decision. */
export function explain(d: DecisionOut): Explanation {
  const held = d.results.filter((r) => r.outcome === 'hold').map(line).concat(d.reasons)
  const denied = d.results.filter((r) => r.outcome === 'deny').map(line)
  const passed = d.results.filter((r) => r.outcome === 'allow').map(line)
  const headline =
    d.verdict === 'allow'
      ? `Paid: all ${passed.length} clauses across ${d.attachments.length} policies passed.`
      : d.verdict === 'deny'
        ? `Denied by ${denied.length} clause${denied.length === 1 ? '' : 's'}.`
        : `Held for ${held.length} reason${held.length === 1 ? '' : 's'}.`
  return { headline, held, denied, passed }
}
