import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { createHistoryFixtureClient, createHistoryHttpClient, normalizePayment } from './client'
import { PaymentHistory } from './PaymentHistory'

describe('history client', () => {
  it('sends demo-auth headers and normalizes snake_case', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ mission_id: 'm1', timeline: [{ seq: 1, kind: 'decision', recorded_at: 'a', valid_at: 'b', late_arrival: true, policy_in_force: { policies: [{ policy_id: 'org-base', version: 2, hash: 'abcdef1234567' }] } }], divergence: { allowed_then: true, recorded_verdict: 'allow', would_now: 'hold', differing_clauses: [{ clause: 'APPROVE-1', then: 'allow', now: 'hold', why: 'limit lowered' }], summary: 's' } }), { status: 200 }))
    const d = await createHistoryHttpClient('/api/history', f as unknown as typeof fetch).payment('m1')
    expect(f).toHaveBeenCalledWith('/api/history/payment/m1', expect.objectContaining({ headers: expect.objectContaining({ 'X-Party-Role': 'admin', 'X-Party-Id': 'console' }) }))
    expect(d.items[0]).toMatchObject({ lateArrival: true, validAt: 'b' })
    expect(d.divergence?.differing[0].clause).toBe('APPROVE-1')
  })
  it('tolerates junk', () => {
    expect(normalizePayment({ timeline: [null, 'x'] }, 'p').items).toEqual([])
  })
})

describe('PaymentHistory', () => {
  it('shows divergence, late arrival, both times and scrubs', async () => {
    render(<PaymentHistory client={createHistoryFixtureClient()} paymentId="p1" />)
    expect((await screen.findAllByTestId('divergence'))[0]).toHaveTextContent('would hold now')
    expect(screen.getAllByText(/APPROVE-1/).length).toBeGreaterThan(0)
    await userEvent.click(screen.getByRole('button', { name: /LATE/ }))
    expect(screen.getByText(/Late arrival/)).toBeInTheDocument()
    expect(screen.getByText('Happened at (valid time)')).toBeInTheDocument()
    expect(screen.getByText('Recorded at (transaction time)')).toBeInTheDocument()
  })
  it('surfaces errors', async () => {
    const client = { kind: 'http' as const, payment: async () => { throw new Error('boom') }, policyAsOf: async () => { throw new Error('x') } }
    render(<PaymentHistory client={client} paymentId="p1" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })
})
