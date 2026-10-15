import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { createAuditFixtureClient } from '../audit'
import type { AuditClient } from '../audit'
import { createFixtureClient } from '../api'
import type { TreasuryState } from '../useTreasury'
import type { Snapshot } from '../types'
import { PartiesView } from './PartiesView'

async function state(over: Partial<Snapshot['inputs']> = {}, run = vi.fn()): Promise<TreasuryState> {
  const snap = await createFixtureClient().getSnapshot('x')
  const s = { ...snap, inputs: { ...snap.inputs, ...over } }
  return { snapshot: s, scenarios: [], policies: [], loading: false, pending: false, error: null, ctx: { missionId: s.missionId, expectedRevision: s.revision }, refresh: async () => {}, run }
}

describe('PartiesView', () => {
  it('all parties side by side, each showing only its own data and log', async () => {
    const audit = createAuditFixtureClient()
    render(<PartiesView t={await state()} audit={audit} pollMs={50} />)
    const sender = screen.getByRole('region', { name: 'Sender' })
    const receiver = screen.getByRole('region', { name: 'Receiver' })
    const driver = screen.getByRole('region', { name: 'Driver' })
    expect(within(sender).getByText('order')).toBeInTheDocument()
    expect(within(receiver).queryByText('order')).not.toBeInTheDocument()
    expect(within(driver).queryByText('amount due')).not.toBeInTheDocument()
    expect(await within(sender).findByText('Sender created order for 42.00 USD')).toBeInTheDocument()
    expect(within(sender).queryByText('Receiver confirmed receipt')).not.toBeInTheDocument()
    expect(await within(receiver).findByText('Receiver confirmed receipt')).toBeInTheDocument()
    expect(within(receiver).queryByText('Driver reported position at depot')).not.toBeInTheDocument()
  })

  it('party logs come from the party-scoped endpoint, not the global one', async () => {
    const audit: AuditClient = { ...createAuditFixtureClient(), party: vi.fn(async () => []), list: vi.fn(async () => []) }
    render(<PartiesView t={await state()} audit={audit} pollMs={50} />)
    await screen.findAllByText('Nothing yet.')
    expect(audit.party).toHaveBeenCalledWith('sender', 0)
    expect(audit.party).toHaveBeenCalledWith('receiver', 0)
    expect(audit.party).toHaveBeenCalledWith('driver', 0)
    expect(audit.list).not.toHaveBeenCalled()
  })

  it('receiver confirms receipt, driver reports position, sender cancels before pickup via typed commands', async () => {
    const run = vi.fn()
    const t = await state({}, run)
    render(<PartiesView t={t} audit={createAuditFixtureClient()} pollMs={50} />)
    const ctx = t.ctx!
    await userEvent.click(within(screen.getByRole('region', { name: 'Receiver' })).getByRole('button', { name: 'Confirm receipt' }))
    expect(run.mock.calls[0][0](ctx)).toMatchObject({ type: 'confirm_receipt', missionId: ctx.missionId })
    const d = within(screen.getByRole('region', { name: 'Driver' }))
    await userEvent.type(d.getByLabelText('Position x'), '3')
    await userEvent.type(d.getByLabelText('Position y'), '4.5')
    await userEvent.click(d.getByRole('button', { name: 'Report position' }))
    expect(run.mock.calls[1][0](ctx)).toMatchObject({ type: 'report_position', payload: { x: 3, y: 4.5, driverId: 'driver-1' } })
    await userEvent.click(within(screen.getByRole('region', { name: 'Sender' })).getByRole('button', { name: 'Cancel before pickup' }))
    expect(run.mock.calls[2][0](ctx)).toMatchObject({ type: 'cancel_requested' })
  })

  it('disables cancel once the delivery has left the depot, and receipt once confirmed', async () => {
    render(<PartiesView t={await state({ location: 'customer', receiptConfirmed: true })} audit={createAuditFixtureClient()} pollMs={50} />)
    expect(screen.getByRole('button', { name: 'Cancel before pickup' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Receipt confirmed' })).toBeDisabled()
  })

  it('single party view hides the others', async () => {
    render(<PartiesView t={await state()} audit={createAuditFixtureClient()} pollMs={50} />)
    await userEvent.click(screen.getByRole('button', { name: 'driver' }))
    expect(screen.queryByRole('region', { name: 'Sender' })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Driver' })).toBeInTheDocument()
  })
})
