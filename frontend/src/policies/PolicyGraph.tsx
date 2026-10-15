import { NODE_H, NODE_W, layoutChain } from './graph'
import type { Attachment } from './types'

interface Props {
  attachments: Attachment[]
  selectedId: string | null
  onSelect: (id: string) => void
  verdict?: string
}

/** Plain SVG node graph of the chained policies. Nodes are keyboard-focusable buttons. */
export function PolicyGraph({ attachments, selectedId, onSelect, verdict }: Props) {
  const g = layoutChain(attachments)
  const byId = new Map(g.nodes.map((n) => [n.id, n]))
  return (
    <svg
      role="group"
      aria-label="Chained policy graph"
      viewBox={`0 0 ${g.width} ${g.height}`}
      width={g.width}
      height={g.height}
      style={{ maxWidth: '100%', height: 'auto' }}
    >
      <defs>
        <marker id="pg-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" fill="currentColor" />
        </marker>
      </defs>
      {g.edges.map((e) => {
        const a = byId.get(e.from)!
        const b = byId.get(e.to)!
        const x1 = a.x + NODE_W
        const y1 = a.y + NODE_H / 2
        const x2 = b.x
        const y2 = b.y + NODE_H / 2
        const mx = (x1 + x2) / 2
        return (
          <path
            key={`${e.from}>${e.to}`}
            data-testid="edge"
            d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`}
            fill="none"
            stroke="currentColor"
            opacity={0.5}
            markerEnd="url(#pg-arrow)"
          />
        )
      })}
      {g.nodes.map((n) => {
        const selected = n.id === selectedId
        const isDecision = n.kind === 'decision'
        return (
          <g
            key={n.id}
            data-testid={isDecision ? 'node-decision' : `node-${n.id}`}
            role={isDecision ? undefined : 'button'}
            tabIndex={isDecision ? undefined : 0}
            aria-pressed={isDecision ? undefined : selected}
            aria-label={isDecision ? undefined : `${n.label}, ${n.sub}`}
            onClick={isDecision ? undefined : () => onSelect(n.id)}
            onKeyDown={isDecision ? undefined : (ev) => (ev.key === 'Enter' || ev.key === ' ') && (ev.preventDefault(), onSelect(n.id))}
            style={{ cursor: isDecision ? 'default' : 'pointer' }}
          >
            <rect
              x={n.x}
              y={n.y}
              width={NODE_W}
              height={NODE_H}
              rx={6}
              fill="none"
              stroke="currentColor"
              strokeWidth={selected ? 3 : 1.25}
              strokeDasharray={isDecision ? '4 3' : undefined}
            />
            <text x={n.x + 10} y={n.y + 19} fontSize={13} fontWeight={600} fill="currentColor">
              {n.label}
            </text>
            <text x={n.x + 10} y={n.y + 36} fontSize={11} fill="currentColor" opacity={0.75}>
              {isDecision && verdict ? `verdict: ${verdict}` : n.sub}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
