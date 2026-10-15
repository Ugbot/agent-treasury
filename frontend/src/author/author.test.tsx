import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AuthorView, NOT_CONNECTED } from './AuthorView'
import { createAuthorFixtureClient } from './client'
import { defaultClause, dollarsToMinor, minorToDollars, sentenceFor } from './sentence'
import type { AuthorClient } from './types'

describe('sentences and money', () => {
  it('rebuilds the sentence per kind from edited fields', () => {
    expect(sentenceFor({ ...defaultClause('max_amount', 'R-1'), params: { limit_minor: 1250 } })).toBe('Hold any payment over $12.50 until a person approves it')
    expect(sentenceFor({ ...defaultClause('allowed_currency', 'R-2'), params: { currencies: ['usd', 'eur'] } })).toBe('Only settle in USD or EUR')
    expect(sentenceFor({ ...defaultClause('forbid_input', 'R-3'), params: { key: 'prohibited_zone' } })).toBe('Deny if the route enters a prohibited zone')
    expect(sentenceFor({ ...defaultClause('require_input', 'R-4'), params: { key: 'receipt_confirmed' } })).toMatch(/receipt/)
  })
  it('converts dollars to integer minor units and back', () => {
    expect(dollarsToMinor('12.50')).toBe(1250)
    expect(dollarsToMinor('$50')).toBe(5000)
    expect(dollarsToMinor('0.29')).toBe(29)
    expect(dollarsToMinor('abc')).toBeNull()
    expect(dollarsToMinor('1.234')).toBeNull()
    expect(minorToDollars(1250)).toBe('12.50')
  })
})

function spied(over: Partial<AuthorClient> = {}) {
  const base = createAuthorFixtureClient()
  const c = { ...base, validate: vi.fn(base.validate), publish: vi.fn(base.publish), draft: vi.fn(base.draft), ...over } as AuthorClient
  return c
}

describe('AuthorView', () => {
  it('debounces validate and calls it once after rapid edits', async () => {
    const c = spied()
    render(<AuthorView client={c} debounceMs={400} />)  // generous so slow CI cannot split the two clicks
    await userEvent.click(screen.getByRole('button', { name: 'Add rule' }))
    await userEvent.click(screen.getByRole('button', { name: 'Add rule' }))
    await waitFor(() => expect(c.validate).toHaveBeenCalled(), { timeout: 2000 })
    await new Promise((r) => setTimeout(r, 500))
    expect((c.validate as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1)
  })

  it('shows the AI-unavailable state and disables drafting', async () => {
    const c = spied({ draft: async () => ({ available: false, unsupported: [], ai_drafted: false }) })
    render(<AuthorView client={c} debounceMs={10} />)
    await userEvent.type(screen.getByLabelText(/Describe the rule/), 'hold anything over $50')
    await userEvent.click(screen.getByRole('button', { name: 'Draft with AI' }))
    expect(await screen.findByText(NOT_CONNECTED)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Draft with AI' })).toBeDisabled()
  })

  it('lists unsupported items, badges AI drafts, and never publishes by itself', async () => {
    const c = spied()
    render(<AuthorView client={c} debounceMs={10} />)
    await userEvent.type(screen.getByLabelText(/Describe the rule/), 'hold anything over $50, give drivers up to $12 for a meal')
    await userEvent.click(screen.getByRole('button', { name: 'Draft with AI' }))
    expect(await screen.findByText(/could not write these as rules/)).toBeInTheDocument()
    expect(screen.getByText(/Allowances for a purpose/)).toBeInTheDocument()
    expect(screen.getByText('AI drafted')).toBeInTheDocument()
    expect(screen.getByText(/Example draft \(fixture\)/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Publish version 1' })).toBeEnabled())
    expect(c.publish).not.toHaveBeenCalled()
  })

  it('keeps publish disabled while invalid, then confirms before publishing', async () => {
    const c = spied()
    render(<AuthorView client={c} debounceMs={10} />)
    expect(screen.getByRole('button', { name: 'Publish version 1' })).toBeDisabled()
    await userEvent.selectOptions(screen.getByLabelText('Kind of rule'), 'allowed_currency')
    await userEvent.click(screen.getByRole('button', { name: 'Add rule' }))
    await userEvent.click(screen.getByText('Edit rule 1'))
    await userEvent.click(screen.getByLabelText('USD'))
    await waitFor(() => expect(screen.getAllByText(/Pick at least one currency/).length).toBeGreaterThan(0))
    expect(screen.getByRole('button', { name: 'Publish version 1' })).toBeDisabled()
    await userEvent.click(screen.getByLabelText('USD'))
    const pub = await screen.findByRole('button', { name: 'Publish version 1' })
    await waitFor(() => expect(pub).toBeEnabled())
    await userEvent.click(pub)
    expect(screen.getByText(/This adds 1 rule\. It applies to payments you attach it to\./)).toBeInTheDocument()
    expect(c.publish).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: /Confirm and publish version 1/ }))
    expect(await screen.findByText(/Published version 1/)).toBeInTheDocument()
    expect(c.publish).toHaveBeenCalledWith(expect.objectContaining({ ai_drafted: false, expected_version: 0 }))
  })
})

describe('http dry-run contract', () => {
  it('sends no situations and maps backend ids to frontend ids', async () => {
    const { createAuthorHttpClient } = await import('./client')
    const recorded = { results: [
      { situation: 'all_in_order', label: 'All in order', verdict: 'allow', clause: null, reason: 'all rules met' },
      { situation: 'receipt_missing', label: 'Receipt missing', verdict: 'hold', clause: 'PAY-1', reason: 'x' },
      { situation: 'prohibited_zone', label: 'Prohibited zone', verdict: 'deny', clause: 'PAY-2', reason: 'x' },
      { situation: 'over_limit', label: 'Over limit', verdict: 'hold', clause: 'R-1', reason: 'x' },
      { situation: 'wrong_currency', label: 'Wrong currency', verdict: 'deny', clause: 'PAY-3', reason: 'x' },
    ] }
    const f = vi.fn(async () => new Response(JSON.stringify(recorded), { status: 200, headers: { 'content-type': 'application/json' } }))
    const rows = await createAuthorHttpClient('/api/author', f as never).dryRun([])
    expect(JSON.parse(String((f.mock.calls[0] as unknown[])[1] && ((f.mock.calls[0] as unknown[])[1] as { body: string }).body))).not.toHaveProperty('situations')
    expect(rows.map((r) => r.situation)).toEqual(['ok', 'receipt', 'zone', 'limit', 'currency'])
  })
})
