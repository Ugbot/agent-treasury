import type { ReactNode } from 'react'
import type { Snapshot } from '../types'
import { describeStatus } from './status'
import { frozenText } from '../freeze/banner'
import type { FreezeState } from '../freeze/types'

/** One sentence that always answers "what is happening and why". The primary action sits beside it. */
export function StatusHeadline({ s, action, freeze }: { s: Snapshot | null; action?: ReactNode; freeze?: FreezeState | null }) {
  const base = describeStatus(s)
  const st = freeze?.active ? { ...base, text: frozenText(freeze), tone: 'deny' as typeof base.tone } : base
  const [lead, ...rest] = st.text.split(': ')
  const hasLead = rest.length > 0 && lead.length < 20
  return (
    <section className={`headline tone-${st.tone}`} aria-label="Current status">
      <p className="headline-text" role="status" data-testid="status-headline">
        {hasLead ? <><strong className="headline-lead">{lead}:</strong> {rest.join(': ')}</> : st.text}
      </p>
      {action && <div className="headline-action">{action}</div>}
    </section>
  )
}
