import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { createWhyHttpClient, normalizeExplanation } from './client'
import { WhyDrawer } from './WhyDrawer'
import type { WhyClient } from './types'

const raw = {
  verdict: 'allow', mode: 'stripe_test', source: 'cognee',
  policy: { id: 'org-base', version: 2, hash: 'abc123' },
  clauses: [{ policy_id: 'org-base', policy_version: 2, clause: 'PAY-1', outcome: 'allow', evidence: { receipt: 'ok' } }, { clause: 'APPROVE-1', outcome: 'hold', evidence: { amount_minor: 9000 } }],
  evidence: ['receipt nonce n1'],
  stripe: { ids: ['pi_123', 'tr_456'], livemode: false },
  audit_seqs: [4, 9],
}

describe('normalizeExplanation', () => {
  it('maps snake_case, clause outcomes and audit links', () => {
    const e = normalizeExplanation(raw, 'p1')
    expect(e).toMatchObject({ verdict: 'allow', source: 'cognee', livemode: false, stripeIds: ['pi_123', 'tr_456'], auditSeqs: [4, 9] })
    expect(e.clauses.map((c) => [c.clauseId, c.passed])).toEqual([['PAY-1', true], ['APPROVE-1', false]])
    expect(e.policy).toEqual({ id: 'org-base', version: 2, hash: 'abc123' })
  })
  it('never infers cognee when the server did not say so', () => {
    expect(normalizeExplanation({ verdict: 'hold' }, 'p').source).toBe('unknown')
  })
})

describe('WhyDrawer', () => {
  it('shows policy, clauses, Stripe ids, source and links to audit entries', async () => {
    const client: WhyClient = { kind: 'http', explain: async (id) => normalizeExplanation(raw, id) }
    const onOpen = vi.fn()
    render(<WhyDrawer client={client} paymentId="p1" onClose={() => {}} onOpenAudit={onOpen} />)
    expect(await screen.findByText('abc123')).toBeInTheDocument()
    expect(screen.getByTestId('why-source')).toHaveTextContent('Retrieved from Cognee memory')
    expect(screen.getByText('pi_123')).toBeInTheDocument()
    expect(screen.getByText('did not pass')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '#9' }))
    expect(onOpen).toHaveBeenCalledWith(9)
  })

  it('shows the audit_log fallback honestly, and the error state', async () => {
    const fb: WhyClient = { kind: 'http', explain: async (id) => normalizeExplanation({ ...raw, source: 'audit_log', source_note: 'Cognee unavailable: not configured' }, id) }
    const { unmount } = render(<WhyDrawer client={fb} paymentId="p" onClose={() => {}} />)
    expect(await screen.findByTestId('why-source')).toHaveTextContent(/audit log.*Cognee unavailable/)
    unmount()
    const bad: WhyClient = { kind: 'http', explain: async () => { throw new Error('404 no such payment') } }
    render(<WhyDrawer client={bad} paymentId="p" onClose={() => {}} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('404 no such payment')
  })

  it('closes on Escape and labels simulated payments', async () => {
    const onClose = vi.fn()
    const sim: WhyClient = { kind: 'http', explain: async (id) => normalizeExplanation({ verdict: 'allow', mode: 'mock', source: 'audit_log' }, id) }
    render(<WhyDrawer client={sim} paymentId="p" onClose={onClose} />)
    expect(await screen.findByText(/This payment was simulated/)).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('http client hits the explanation endpoint', async () => {
    const f = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: raw })))
    const c = createWhyHttpClient('', f as unknown as typeof fetch)
    expect((await c.explain('a/b')).verdict).toBe('allow')
    expect(f.mock.calls[0][0]).toBe('/api/payments/a%2Fb/explanation')
  })
})
