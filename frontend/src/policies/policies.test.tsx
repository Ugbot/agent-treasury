import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { createPoliciesFixtureClient, createPoliciesHttpClient } from './client'
import { explain, layoutChain } from './graph'
import { PoliciesView } from './PoliciesView'
import { clauseSentence } from './clauses'
import { buildLayers } from './layers'
import type { Attachment } from './types'

const att = (id: string, t: Attachment['target_type'], p: string, m = false): Attachment => ({
  attachment_id: id, target_type: t, target_id: t, policy_id: p, policy_version: 1, mandatory: m,
})

describe('layoutChain', () => {
  it('chains scopes left to right into the decision node', () => {
    const g = layoutChain([att('a', 'organisation', 'org-base', true), att('b', 'card', 'cap'), att('c', 'card', 'ins')])
    expect(g.edges.map((e) => `${e.from}>${e.to}`)).toEqual(['a>b', 'a>c', 'b>decision', 'c>decision'])
    const x = (id: string) => g.nodes.find((n) => n.id === id)!.x
    expect(x('a')).toBeLessThan(x('b'))
    expect(x('b')).toBeLessThan(x('decision'))
  })
})

describe('explain', () => {
  it('describes held and paid', () => {
    const held = explain({
      verdict: 'hold', input_revision: 1, effective_policy_hash: 'h', attachments: [],
      results: [{ policy_id: 'org-base', policy_version: 1, clause: 'PAY-1', outcome: 'hold', evidence: { missing: 'receipt_confirmed' } }],
      reasons: [],
    })
    expect(held.headline).toMatch(/Held/)
    expect(held.held[0]).toContain('missing=receipt_confirmed')
    expect(explain({ verdict: 'allow', input_revision: 1, effective_policy_hash: 'h', attachments: [['a', 'p', 1, false]], results: [], reasons: [] }).headline).toMatch(/Paid/)
  })
})

describe('http client', () => {
  it('sends expected_version on attach and detach', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ target_version: 2 }), { status: 200 }))
    const c = createPoliciesHttpClient('/api/policies', f as unknown as typeof fetch)
    await c.attach({ type: 'card', id: 'card-1' }, 'cap', 1, 1)
    await c.detach('att-9', 2)
    const [u1, o1] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(u1).toBe('/api/policies/attachments')
    expect(JSON.parse(o1.body as string)).toMatchObject({ target_type: 'card', policy_id: 'cap', expected_version: 1 })
    expect((f.mock.calls[1] as unknown as [string])[0]).toBe('/api/policies/attachments/att-9?expected_version=2')
  })

  it('surfaces 409 as a conflict error', async () => {
    const f = async () => new Response(JSON.stringify({ detail: 'version conflict' }), { status: 409 })
    const c = createPoliciesHttpClient('', f as unknown as typeof fetch)
    await expect(c.detach('x', 0)).rejects.toMatchObject({ isConflict: true, message: 'version conflict' })
  })
})

describe('clauseSentence', () => {
  const cl = (kind: string, params: Record<string, unknown>, prose = 'prose') => ({ id: 'X-1', prose, kind, params })
  it('writes every clause kind in plain English', () => {
    expect(clauseSentence(cl('max_amount', { limit_minor: 5000 }))).toBe('Hold any payment over $50.00 until a person approves it')
    expect(clauseSentence(cl('require_input', { key: 'receipt_confirmed' }))).toBe('Release only after the customer confirms receipt')
    expect(clauseSentence(cl('require_input', { key: 'gate_code' }))).toBe('Hold until gate code is confirmed')
    expect(clauseSentence(cl('forbid_input', { key: 'prohibited_zone' }))).toBe('Deny if the route enters a prohibited zone')
    expect(clauseSentence(cl('forbid_input', { key: 'sanctioned_party' }))).toBe('Deny if sanctioned party is present')
    expect(clauseSentence(cl('allowed_currency', { currencies: ['usd'] }))).toBe('Only settle in USD')
    expect(clauseSentence(cl('allowed_currency', { currencies: ['usd', 'eur', 'gbp'] }))).toBe('Only settle in USD, EUR or GBP')
  })
  it('falls back to prose when params are missing', () => {
    expect(clauseSentence(cl('max_amount', {}, 'Never exceed the remaining refundable amount'))).toBe('Never exceed the remaining refundable amount')
  })
})

describe('buildLayers', () => {
  it('orders layers organisation, job, card, payment with a running total', () => {
    const chain = [
      { type: 'organisation' as const, id: 'organisation' }, { type: 'mission' as const, id: 'mission' },
      { type: 'card' as const, id: 'card' }, { type: 'payment' as const, id: 'payment' },
    ]
    const b = (id: string, n: number) => ({
      policy_id: id, version: 1, title: id, content_hash: 'h',
      clauses: Array.from({ length: n }, (_, i) => ({ id: `${id}-${i}`, prose: 'p', kind: 'max_amount', params: {} })),
    })
    const layers = buildLayers([att('a', 'organisation', 'o', true), att('b', 'payment', 'p')], [b('o', 4), b('p', 2)], chain)
    expect(layers.map((l) => l.type)).toEqual(['organisation', 'mission', 'card', 'payment'])
    expect(layers.map((l) => l.adds)).toEqual([4, 0, 0, 2])
    expect(layers.map((l) => l.inForce)).toEqual([4, 4, 4, 6])
  })
})

describe('PoliciesView', () => {
  it('shows the stack in order with plain-English rules and a live preview', async () => {
    const user = userEvent.setup()
    render(<PoliciesView client={createPoliciesFixtureClient()} />)
    const stack = await screen.findByRole('list', { name: /Rule stack/ })
    const titles = within(stack).getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    expect(titles.slice(0, 4).map((t) => t?.replace('Locked', ''))).toEqual(['Organisation rules', 'Job policies', 'Card and counterparty', 'This payment'])
    expect(within(stack).getAllByText('Only settle in USD').length).toBeGreaterThan(0)
    expect(within(stack).getAllByText('Deny if the route enters a prohibited zone').length).toBeGreaterThan(0)
    expect(screen.queryByText(/limit_minor/)).toBeNull()
    expect(screen.getByText(/deny beats hold beats allow/i)).toBeTruthy()

    await waitFor(() => expect(screen.getByTestId('verdict').textContent).toBe('Allow'))
    await user.click(screen.getByRole('button', { name: 'Receipt missing' }))
    await waitFor(() => expect(screen.getByTestId('verdict').textContent).toBe('Hold'))
    expect(screen.getByTestId('deciding').textContent).toContain('PAY-1')
    await user.click(screen.getByRole('button', { name: 'Prohibited zone' }))
    await waitFor(() => expect(screen.getByTestId('verdict').textContent).toBe('Deny'))
    expect(screen.getByTestId('deciding').textContent).toContain('TRAVEL-1')
    await user.click(screen.getByRole('button', { name: 'Wrong currency' }))
    await waitFor(() => expect(screen.getByTestId('deciding').textContent).toContain('CURRENCY-1'))
  })

  it('explains an attach, adds the rule to the payment layer, then detaches', async () => {
    const user = userEvent.setup()
    render(<PoliciesView client={createPoliciesFixtureClient()} />)
    const lib = await screen.findByRole('list', { name: 'Policy library' })
    await user.click(within(lib).getByText('Card spend cap'))
    expect(screen.getByText(/This would add 1 rule to payment payment-1 and can only make decisions stricter/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Attach to this payment' }))
    await waitFor(() => expect(screen.getByText(/Already attached to payment payment-1/)).toBeTruthy())
    const stack = screen.getByRole('list', { name: /Rule stack/ })
    expect(within(stack).getAllByText('Hold any payment over $20.00 until a person approves it').length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: 'Over limit' }))
    await waitFor(() => expect(screen.getByTestId('verdict').textContent).toBe('Hold'))

    await user.click(screen.getByRole('button', { name: /Detach from this payment/ }))
    await waitFor(() => expect(within(stack).queryByText(/over \$20.00/)).toBeNull())
  })

  it('cannot detach or re-attach the mandatory org policy, and says why', async () => {
    const user = userEvent.setup()
    render(<PoliciesView client={createPoliciesFixtureClient()} />)
    const lib = await screen.findByRole('list', { name: 'Policy library' })
    await user.click(within(lib).getByText('Organisation base policy'))
    const detach = screen.getByRole('button', { name: /Detach from organisation rules/i }) as HTMLButtonElement
    expect(detach.disabled).toBe(true)
    expect(screen.getByText(/cannot be removed/)).toBeTruthy()
    expect((screen.getByRole('button', { name: /Attach to/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('lists an inherited attachment once even when every target returns it', async () => {
    const client = createPoliciesFixtureClient()
    const eff = client.effective.bind(client)
    const org = await eff({ type: 'organisation', id: 'org' })
    client.effective = async (t) => { const e = await eff(t); return { ...e, attachments: [...org.attachments, ...e.attachments.filter((a) => a.target_type !== 'organisation')] } }
    render(<PoliciesView client={client} />)
    const stack = await screen.findByRole('list', { name: /Rule stack/ })
    expect(within(stack).getAllByRole('button', { name: /Organisation base policy/ })).toHaveLength(1)
    expect(stack.textContent).toContain('Adds 4 rules, 4 in force')
  })

  it('shows the memory-unavailable note without blocking decisions', async () => {
    const client = createPoliciesFixtureClient()
    const list = client.listPolicies.bind(client)
    client.listPolicies = async () => (await list()).map((b) => ({ ...b, memory: { ok: false, status: 'unavailable', message: 'Cognee is not configured.', source_ref: null } }))
    render(<PoliciesView client={client} />)
    expect(await screen.findByText(/Policy memory: not connected/)).toBeTruthy()
    expect(screen.getByText(/still enforced from the local ledger/)).toBeTruthy()
    await waitFor(() => expect(screen.getByTestId('verdict').textContent).toBe('Allow'))
  })

  it('shows an error with retry when loading fails', async () => {
    const user = userEvent.setup()
    const client = createPoliciesFixtureClient()
    const list = client.listPolicies.bind(client)
    let fail = true
    client.listPolicies = async () => { if (fail) throw new Error('boom'); return list() }
    render(<PoliciesView client={client} />)
    expect((await screen.findByRole('alert')).textContent).toMatch(/boom/)
    fail = false
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('list', { name: /Rule stack/ })).toBeTruthy()
  })

  it('gives plain direction on a stale version and reloads', async () => {
    const user = userEvent.setup()
    const client = createPoliciesFixtureClient()
    const real = client.attach.bind(client)
    client.attach = async (...a) => {
      await real(a[0], 'travel-insured', 1, a[3])
      return real(...a)
    }
    render(<PoliciesView client={client} />)
    const lib = await screen.findByRole('list', { name: 'Policy library' })
    await user.click(within(lib).getByText('Card spend cap'))
    await user.click(screen.getByRole('button', { name: 'Attach to this payment' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/Someone else changed the rules.*try again/)
    expect((await screen.findAllByText('Hold until the driver is insured')).length).toBeGreaterThan(0)
  })
})
