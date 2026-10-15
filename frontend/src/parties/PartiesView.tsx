import { useState } from 'react'
import type { AuditClient } from '../audit/types'
import type { TreasuryState } from '../useTreasury'
import { PARTIES, PartyView } from './PartyView'
import type { Party } from './PartyView'

type Pick = Party | 'all'

export function PartiesView({ t, audit, pollMs }: { t: TreasuryState; audit: AuditClient; pollMs?: number }) {
  const [pick, setPick] = useState<Pick>('all')
  const shown = pick === 'all' ? PARTIES : [pick]
  return (
    <div className="parties">
      <div className="row subnav" role="group" aria-label="Party">
        {(['all', ...PARTIES] as Pick[]).map((p) => (
          <button key={p} type="button" aria-pressed={pick === p} onClick={() => setPick(p)}>{p === 'all' ? 'All parties' : p}</button>
        ))}
      </div>
      <div className={`party-grid ${pick === 'all' ? 'side-by-side' : ''}`}>
        {shown.map((p) => <PartyView key={p} party={p} t={t} audit={audit} pollMs={pollMs} />)}
      </div>
    </div>
  )
}
