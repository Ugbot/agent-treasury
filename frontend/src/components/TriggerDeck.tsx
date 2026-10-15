import { useState } from 'react'
import { attachPolicy, confirmReceipt, selectScenario, setAmount, setInsurance, setLocation } from '../api'
import type { TreasuryState } from '../useTreasury'

export function TriggerDeck({ t }: { t: TreasuryState }) {
  const s = t.snapshot
  const [amount, setAmountText] = useState('')
  const [policyId, setPolicyId] = useState('')
  const [scenario, setScenario] = useState('')
  const disabled = !s || t.pending || t.loading
  // The server requires the recipient to be at the destination before it accepts a receipt.
  const confirm = async () => {
    if (s && s.mode !== 'fixture' && s.inputs.location !== 'customer') await t.run((c) => setLocation(c, 'customer'))
    await t.run((c) => confirmReceipt(c))
  }
  const pounds = amount === '' ? NaN : Number(amount)
  const valid = Number.isFinite(pounds) && pounds > 0 && Math.round(pounds * 100) <= 100_000_000

  return (
    <section aria-labelledby="deck-h" className="panel" aria-busy={t.pending}>
      <h2 id="deck-h">Trigger deck</h2>
      <p className="hint">Buttons send commands. The backend decides.</p>

      <form onSubmit={(e) => { e.preventDefault(); if (valid) void t.run((c) => setAmount(c, Math.round(pounds * 100))) }}>
        <label htmlFor="amount">Amount ({(s?.inputs.currency ?? "usd").toUpperCase()})</label>
        <div className="row">
          <input id="amount" inputMode="decimal" value={amount} placeholder={s ? String(s.inputs.amountMinor / 100) : ''} onChange={(e) => setAmountText(e.target.value)} disabled={disabled} aria-invalid={amount !== '' && !valid} />
          <button type="submit" disabled={disabled || !valid}>Set amount</button>
        </div>
      </form>

      <fieldset disabled={disabled}>
        <legend>Insurance</legend>
        <div className="row">
          <button type="button" aria-pressed={s?.inputs.insured === true} onClick={() => void t.run((c) => setInsurance(c, true))}>Insured</button>
          <button type="button" aria-pressed={s?.inputs.insured === false} onClick={() => void t.run((c) => setInsurance(c, false))}>Uninsured</button>
        </div>
      </fieldset>

      <div>
        <label htmlFor="location">Location</label>
        <select id="location" disabled={disabled} value={s?.inputs.location ?? ''} onChange={(e) => void t.run((c) => setLocation(c, e.target.value))}>
          {['depot', 'customer', 'fuel-stop', 'prohibited'].map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
      </div>

      <div>
        <button type="button" disabled={disabled || s?.inputs.receiptConfirmed} onClick={() => void confirm()}>
          {s?.inputs.receiptConfirmed ? 'Receipt confirmed' : 'Confirm receipt'}
        </button>
      </div>

      <div>
        <label htmlFor="policy">Attach policy to payment</label>
        <div className="row">
          <select id="policy" value={policyId} disabled={disabled || t.policies.length === 0} onChange={(e) => setPolicyId(e.target.value)}>
            <option value="">Choose policy</option>
            {t.policies.map((p) => <option key={p.policyId} value={p.policyId}>{p.title} v{p.version}</option>)}
          </select>
          <button type="button" disabled={disabled || !policyId} onClick={() => { const p = t.policies.find((x) => x.policyId === policyId); if (p) void t.run((c) => attachPolicy(c, p.policyId, p.version)) }}>Attach</button>
        </div>
      </div>

      <div>
        <label htmlFor="scenario">Scenario</label>
        <div className="row">
          <select id="scenario" value={scenario} disabled={disabled || t.scenarios.length === 0} onChange={(e) => setScenario(e.target.value)}>
            <option value="">Choose scenario</option>
            {t.scenarios.map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}
          </select>
          <button type="button" disabled={disabled || !scenario} onClick={() => s && void t.run((c) => selectScenario(c, scenario, s.mode))}>Start</button>
        </div>
      </div>
    </section>
  )
}
