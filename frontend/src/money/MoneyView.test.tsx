import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { MoneyView } from './MoneyView'
import { fixtureMoney, flowClass, gbp, netMinor, redact, statusLabel } from './model'

describe('money model', () => {
  it('formats minor units and splits gross/retention/fees/net', () => {
    expect(gbp(4200)).toBe('£42.00')
    const f = fixtureMoney().flows.find((x) => x.id === 'f2')!
    expect(netMinor(f)).toBe(8680)
  })
  it('invoice-created never looks paid', () => {
    const f = fixtureMoney().flows.find((x) => x.kind === 'invoice')!
    expect(statusLabel(f)).toBe('Invoice issued, unpaid')
    expect(statusLabel(f)).not.toMatch(/^Invoice paid/)
  })
  it('redacts secrets and rejects livemode', () => {
    expect(redact('x sk_live_abc123 whsec_zzz')).toBe('x [redacted] [redacted]')
    const f = { ...fixtureMoney().flows[1], livemode: true }
    expect(statusLabel(f)).toMatch(/REJECTED/)
    expect(flowClass(f)).toBe('denied')
  })
})

describe('MoneyView', () => {
  it('partialAndUnknownStates: shows distinct states, ids and livemode=false', () => {
    render(<MoneyView />)
    expect(screen.getByText(/Fixture data/)).toBeInTheDocument()
    expect(screen.getByText('Pending (not yet spendable)')).toBeInTheDocument()
    expect(screen.getByText('Available (spendable)')).toBeInTheDocument()
    expect(screen.getByText('Transfer partially settled')).toBeInTheDocument()
    expect(screen.getByText('Payout unknown')).toBeInTheDocument()
    expect(screen.getByText('Refund compensation required')).toBeInTheDocument()
    expect(screen.getByText('Authorized, not captured')).toBeInTheDocument()
    expect(screen.getAllByText(/livemode=false/).length).toBeGreaterThan(3)
    expect(screen.getAllByText(/tr_fixture1/).length).toBeGreaterThan(0)
  })
  it('renders allowance gauges with accessible values', () => {
    render(<MoneyView />)
    const m = screen.getByRole('meter', { name: 'Fuel card allowance used' })
    expect(m).toHaveAttribute('aria-valuetext', '£42.00 of £60.00')
  })
  it('inspector shows policy, clauses, receipt and lineage', async () => {
    render(<MoneyView />)
    await userEvent.click(screen.getByRole('button', { name: /platform → merchant/ }))
    const i = screen.getByLabelText('Evidence inspector')
    expect(within(i).getByText(/travel-insured v2/)).toBeInTheDocument()
    expect(within(i).getByText(/transfer tr_fixture1/)).toBeInTheDocument()
    expect(within(i).getByText(/APPROVE-1/)).toBeInTheDocument()
    expect(within(i).getByText(/plan plan-1/)).toBeInTheDocument()
  })
})
