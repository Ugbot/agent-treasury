import { useCallback, useEffect, useState } from 'react'
import type { FreezeClient, FreezeState } from './types'
import { frozenText } from './banner'
import './freeze.css'

export function useFreeze(client: FreezeClient) {
  const [state, setState] = useState<FreezeState | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { client.get().then(setState).catch(() => setState(null)) }, [client])
  const set = useCallback(async (active: boolean, reason: string) => {
    try { setError(null); setState(await client.set(active, reason)) } catch (e) { setError(e instanceof Error ? e.message : String(e)); throw e }
  }, [client])
  return { state, error, set }
}

export function FreezeBanner({ state }: { state: FreezeState | null }) {
  if (!state?.active) return null
  return (
    <div className="fz-banner" role="alert" data-testid="freeze-banner">
      <strong>{frozenText(state)}</strong>
      {state.reason && <span> Reason: {state.reason}</span>}
    </div>
  )
}

/** App bar control: a confirm step with a reason field before payments are frozen or released. */
export function FreezeControl({ state, error, onSet }: { state: FreezeState | null; error?: string | null; onSet: (active: boolean, reason: string) => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const frozen = !!state?.active
  const verb = frozen ? 'Unfreeze payments' : 'Freeze payments'
  const confirm = async () => {
    setBusy(true)
    try { await onSet(!frozen, reason.trim()); setOpen(false); setReason('') } catch { /* error shown */ } finally { setBusy(false) }
  }
  return (
    <div className="fz">
      <button type="button" className={frozen ? 'fz-btn fz-on' : 'fz-btn'} onClick={() => setOpen(true)} aria-haspopup="dialog">{verb}</button>
      {open && (
        <div className="fz-pop" role="dialog" aria-label={verb}>
          <p>{frozen ? 'Release the freeze? Payments will be judged by their normal policies again.' : 'Freeze all payments? Every payment will be held until an approver unfreezes.'}</p>
          <label htmlFor="fz-reason">Reason (recorded in the audit log)</label>
          <input id="fz-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          {error && <p className="fz-err" role="alert">{error}</p>}
          <div className="fz-actions">
            <button type="button" className="primary" disabled={busy || !reason.trim()} onClick={() => void confirm()}>Confirm: {verb.toLowerCase()}</button>
            <button type="button" className="quiet" onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  )
}
