import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { createJourneyFixtureClient, createJourneyHttpClient } from './client'
import { JourneyView } from './JourneyView'
import { money, toMinor } from './types'

describe('money helpers', () => {
  it('parses decimals into integer minor units and rejects junk', () => {
    expect(toMinor('12.5')).toBe(1250)
    expect(toMinor('200')).toBe(20000)
    expect(toMinor('1.234')).toBeNull()
    expect(toMinor('-3')).toBeNull()
    expect(toMinor('abc')).toBeNull()
    expect(money(2505)).toBe('$25.05')
  })
})

describe('http client', () => {
  it('posts a typed job and surfaces backend errors', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ detail: 'currency eur is not permitted by policy CURRENCY-1' }), { status: 422 }))
    const c = createJourneyHttpClient('/api', f as unknown as typeof fetch)
    await expect(c.createJob({
      pickup: 'a', dropoff: 'b', goods_value_minor: 1, price_minor: 2, delivery_fee_minor: 0, deadline: 'x', insurance_required: false,
    })).rejects.toThrow(/CURRENCY-1/)
    expect(f).toHaveBeenCalledWith('/api/jobs', expect.objectContaining({ method: 'POST' }))
  })
})

describe('JourneyView', () => {
  it('validates the form before calling the backend', async () => {
    const client = createJourneyFixtureClient()
    const spy = vi.spyOn(client, 'createJob')
    render(<JourneyView client={client} pollMs={5} />)
    await userEvent.clear(screen.getByLabelText('Price (USD)'))
    await userEvent.type(screen.getByLabelText('Price (USD)'), '0')
    await userEvent.click(screen.getByRole('button', { name: /save and choose/i }))
    expect(screen.getByText('Enter an amount above zero')).toBeInTheDocument()
    expect(spy).not.toHaveBeenCalled()
  })

  it('walks define, attach, preview, dispatch and narrates until paid', async () => {
    render(<JourneyView client={createJourneyFixtureClient()} pollMs={5} />)
    expect(screen.getByText(/Fixture mode/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /save and choose/i }))

    // step 2: org rule is locked and listed; attaching adds a job rule
    const rules = await screen.findByTestId('effective-rules')
    expect(within(rules).getByText('Organisation base policy')).toBeInTheDocument()
    expect(within(rules).getByText('ORG, LOCKED')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Attach Insurance required' }))
    await waitFor(() => expect(within(screen.getByTestId('effective-rules')).getByText('Insurance required')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Attach Insurance required' })).toBeNull()

    // step 3: preview names the verdict, clause and a plain reason
    await userEvent.click(screen.getByRole('button', { name: 'Preview' }))
    const row = await screen.findByTestId('preview-insurance_lapsed')
    expect(within(row).getByText('hold')).toBeInTheDocument()
    expect(within(row).getByText('INSURE-1')).toBeInTheDocument()
    expect(within(screen.getByTestId('preview-prohibited_zone')).getByText('deny')).toBeInTheDocument()

    // step 4: dispatch and watch
    await userEvent.click(screen.getByRole('button', { name: 'Dispatch job' }))
    await waitFor(() => expect(screen.getByTestId('watch-status')).toHaveTextContent('paid'), { timeout: 3000 })
    const narr = screen.getByTestId('narration')
    expect(within(narr).getByText(/Job dispatched/)).toBeInTheDocument()
    expect(within(narr).getByText(/delivery receipt has not been confirmed/)).toBeInTheDocument()
    expect(within(narr).getAllByText('SIMULATED').length).toBeGreaterThan(0)
    expect(within(narr).getAllByText('MOCK').length).toBeGreaterThan(0)
  })

  it('shows a conflict error from the backend', async () => {
    const client = createJourneyFixtureClient()
    vi.spyOn(client, 'attach').mockRejectedValue(Object.assign(new Error('stale target version'), { status: 409 }))
    render(<JourneyView client={client} pollMs={5} />)
    await userEvent.click(screen.getByRole('button', { name: /save and choose/i }))
    await userEvent.click(await screen.findByRole('button', { name: 'Attach Fuel and meal allowance' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/stale target version/)
  })
})
