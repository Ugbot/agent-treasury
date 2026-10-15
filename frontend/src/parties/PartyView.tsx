import { useState } from 'react'
import { cancelRequested, confirmReceipt, setLocation, reportPosition } from '../api'
import { useAuditFeed } from '../audit/useAuditFeed'
import type { AuditClient } from '../audit/types'
import { ModeChip, VerdictChip } from '../components/ModeChip'
import { formatMoney } from '../lib/wire'
import type { TreasuryState } from '../useTreasury'
import type { Snapshot } from '../types'

export type Party = 'sender' | 'receiver' | 'driver'
export const PARTIES: Party[] = ['sender', 'receiver', 'driver']

const TITLE: Record<Party, string> = { sender: 'Sender', receiver: 'Receiver', driver: 'Driver' }
const BLURB: Record<Party, string> = {
  sender: 'Pays for the delivery. Sees the order, the policy verdict on their payment, and can cancel before pickup.',
  receiver: 'Gets the delivery. Sees what is due and confirms receipt, which releases payment.',
  driver: 'Carries the delivery. Sees their assignment and payout, and reports position.',
}
const PICKED_UP = (s: Snapshot) => s.inputs.location !== 'depot'

function PartyData({ party, s }: { party: Party; s: Snapshot }) {
  const op = s.operation
  const cur = s.inputs.currency
  if (party === 'sender') {
    return (
      <dl>
        <dt>order</dt><dd>{formatMoney(s.inputs.amountMinor, cur)}</dd>
        <dt>verdict</dt><dd>{s.decision ? <VerdictChip verdict={s.decision.verdict} /> : 'pending'}</dd>
        <dt>payment</dt><dd>{op ? `${op.status}${op.providerObjectId ? ` (${op.providerObjectId})` : ''}` : 'not released'}</dd>
        <dt>mode</dt><dd><ModeChip mode={s.mode} /></dd>
      </dl>
    )
  }
  if (party === 'receiver') {
    return (
      <dl>
        <dt>amount due</dt><dd>{formatMoney(s.inputs.amountMinor, cur)}</dd>
        <dt>receipt</dt><dd>{s.inputs.receiptConfirmed ? 'confirmed' : 'not confirmed'}</dd>
      </dl>
    )
  }
  return (
    <dl>
      <dt>position</dt><dd>{s.inputs.location}</dd>
      <dt>assignment</dt><dd>v{s.assignmentVersion ?? 0}</dd>
      <dt>payout</dt><dd>{op ? op.status : 'not released'}</dd>
    </dl>
  )
}

export function PartyView({ party, t, audit, pollMs }: { party: Party; t: TreasuryState; audit: AuditClient; pollMs?: number }) {
  const s = t.snapshot
  const feed = useAuditFeed(audit, { party, pollMs })
  const [x, setX] = useState('')
  const [y, setY] = useState('')
  const disabled = !s || t.pending
  // The server requires the recipient to be at the destination before it accepts a receipt.
  const confirm = async () => {
    if (s && s.mode !== 'fixture' && s.inputs.location !== 'customer') await t.run((c) => setLocation(c, 'customer'))
    await t.run((c) => confirmReceipt(c))
  }
  const xn = Number(x)
  const yn = Number(y)
  const posOk = x !== '' && y !== '' && Number.isFinite(xn) && Number.isFinite(yn)
  const h = `party-${party}-h`

  return (
    <section className={`panel party party-${party}`} aria-labelledby={h} data-party={party}>
      <h2 id={h}>{TITLE[party]}</h2>
      <p className="hint">{BLURB[party]}</p>
      {s ? <PartyData party={party} s={s} /> : <p>No mission loaded.</p>}

      <div className="actions" role="group" aria-label={`${TITLE[party]} actions`}>
        {party === 'receiver' && (
          <button type="button" disabled={disabled || s?.inputs.receiptConfirmed} onClick={() => void confirm()}>
            {s?.inputs.receiptConfirmed ? 'Receipt confirmed' : 'Confirm receipt'}
          </button>
        )}
        {party === 'sender' && (
          <button type="button" disabled={disabled || (s ? PICKED_UP(s) : true)} onClick={() => void t.run((c) => cancelRequested(c, 'sender cancelled before pickup'))}
            title={s && PICKED_UP(s) ? 'Pickup already happened' : undefined}>
            Cancel before pickup
          </button>
        )}
        {party === 'driver' && (
          <form className="row" onSubmit={(e) => { e.preventDefault(); if (posOk) void t.run((c) => reportPosition(c, 'driver-1', xn, yn, s?.assignmentVersion ?? 0)) }}>
            <input aria-label="Position x" inputMode="decimal" placeholder="x" value={x} onChange={(e) => setX(e.target.value)} disabled={disabled} />
            <input aria-label="Position y" inputMode="decimal" placeholder="y" value={y} onChange={(e) => setY(e.target.value)} disabled={disabled} />
            <button type="submit" disabled={disabled || !posOk}>Report position</button>
          </form>
        )}
        <p className="hint">The server decides. These send commands only.</p>
      </div>

      <h3>{TITLE[party]} activity <span className="hint">({feed.transport === 'sse' ? 'live' : feed.transport === 'polling' ? 'polling 1s' : '...'})</span></h3>
      {feed.error && <div role="alert" className="error inline">{feed.error}</div>}
      {feed.entries.length === 0 ? <p className="hint">{feed.loading ? 'Loading...' : 'Nothing yet.'}</p> : (
        <ol className="feed plog" aria-live="polite" aria-label={`${TITLE[party]} activity log`}>
          {[...feed.entries].reverse().map((e) => (
            <li key={e.seq}>
              <span className="t">#{e.seq} {e.ts.slice(11, 19)}</span> <strong>{e.kind}</strong> <ModeChip mode={e.mode} />
              <div>{e.summary}</div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
