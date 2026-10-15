import { useMemo } from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FreezeBanner, FreezeControl, useFreeze, createFreezeFixtureClient, frozenText, createFreezeHttpClient } from './index'
import { clauseSentence } from '../policies/clauses'
import { StatusHeadline } from '../shell/StatusHeadline'

function Harness() {
  const fc = useMemo(() => createFreezeFixtureClient(), [])
  const f = useFreeze(fc)
  return <><FreezeControl state={f.state} error={f.error} onSet={f.set} /><FreezeBanner state={f.state} /></>
}

describe('sentences', () => {
  it('payees', () => expect(clauseSentence({ id: 'a', prose: 'x', kind: 'allowed_counterparties', params: { recipients: ['Acme Fuel', 'Driver 1'], categories: [] } })).toBe('Only pay these payees: Acme Fuel, Driver 1'))
  it('velocity', () => expect(clauseSentence({ id: 'a', prose: 'x', kind: 'velocity_limit', params: { max_payments: 3, window_seconds: 600 } })).toBe('No more than 3 payments every 10 minutes'))
  it('freeze', () => expect(clauseSentence({ id: 'a', prose: 'x', kind: 'payment_freeze', params: { active: true } })).toBe('Payments are frozen'))
})

describe('freeze flow', () => {
  it('needs a reason, confirms, then shows the banner and unfreezes', async () => {
    render(<Harness />)
    expect(screen.queryByTestId('freeze-banner')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Freeze payments' }))
    const ok = screen.getByRole('button', { name: /Confirm/ })
    expect(ok).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: 'suspicious' } })
    fireEvent.click(ok)
    const b = await screen.findByTestId('freeze-banner')
    expect(b.textContent).toContain('Payments are frozen by approver since')
    expect(b.textContent).toContain('every payment is held')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    fireEvent.click(await screen.findByRole('button', { name: 'Unfreeze payments' }))
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: 'resolved' } })
    fireEvent.click(screen.getByRole('button', { name: /Confirm/ }))
    await waitFor(() => expect(screen.queryByTestId('freeze-banner')).toBeNull())
  })
  it('cancel changes nothing', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Freeze payments' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('headline says frozen', () => {
    const f = { active: true, version: 2, since: null, by: 'Ann', reason: null }
    render(<StatusHeadline s={null} freeze={f} />)
    expect(screen.getByTestId('status-headline').textContent).toContain('frozen by Ann')
    expect(frozenText(f)).toContain('every payment is held')
  })
  it('http client falls back on 404 and sends approver header', async () => {
    const calls: RequestInit[] = []
    const fx = (async (_u: string, init?: RequestInit) => { calls.push(init ?? {}); return new Response('{}', { status: 404 }) }) as unknown as typeof fetch
    const c = createFreezeHttpClient('', fx)
    expect((await c.get()).active).toBe(false)
    expect((await c.set(true, 'r')).active).toBe(true)
    expect((calls[1].headers as Record<string, string>)['X-Actor-Role']).toBe('approver')
  })
})
