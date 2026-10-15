import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { createScenariosHttpClient, normalizeStep } from './client'
import { ScenariosPanel } from './ScenariosPanel'
import type { ScenariosClient } from './types'

const base = (over: Partial<ScenariosClient> = {}): ScenariosClient => ({
  kind: 'http',
  list: async () => [{ id: 's1', title: 'Scenario one' }, { id: 's2', title: 'Scenario two' }],
  run: async (id, mode) => ({ scenarioId: id, missionId: 'm9', status: 'completed', mode, steps: [{ name: 'pay', status: 'confirmed', detail: null }], unavailable: null }),
  step: async () => normalizeStep({
    agent: 'llm', mode: 'stripe_test', audit_seq: 12,
    proposal: { action: 'pay_driver', amount_minor: 1500, currency: 'usd', recipient: 'driver-1', rationale: 'delivery done' },
    verdict: { verdict: 'hold', executed: false, clauses: [{ clause: 'APPROVE-1', outcome: 'hold', evidence: { limit_minor: 1000 } }] },
  }),
  ...over,
})

describe('ScenariosPanel', () => {
  it('runs a scenario with the chosen mode and reports the run', async () => {
    const run = vi.fn(base().run)
    const onRan = vi.fn()
    render(<ScenariosPanel client={base({ run })} onRan={onRan} />)
    await userEvent.selectOptions(await screen.findByLabelText('Payment mode'), 'stripe_test')
    await userEvent.click((await screen.findAllByRole('button', { name: 'Run' }))[1])
    expect(run).toHaveBeenCalledWith('s2', 'stripe_test')
    expect(await screen.findByText('completed')).toBeInTheDocument()
    expect(screen.getByText('STRIPE TEST')).toBeInTheDocument()
    expect(onRan).toHaveBeenCalled()
  })

  it('shows the agent proposal and the policy verdict side by side', async () => {
    const onAudit = vi.fn()
    render(<ScenariosPanel client={base()} missionId="m1" onAudit={onAudit} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Step agent' }))
    const prop = await screen.findByLabelText('Agent proposal')
    const ver = screen.getByLabelText('Policy verdict')
    expect(within(prop).getByText('pay_driver')).toBeInTheDocument()
    expect(within(prop).getByText('$15.00')).toBeInTheDocument()
    expect(within(ver).getByText('hold', { selector: 'p' })).toBeInTheDocument()
    expect(within(ver).getByText('Not executed.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'audit #12' }))
    expect(onAudit).toHaveBeenCalledWith(12)
  })

  it('surfaces an unavailable agent instead of faking a step', async () => {
    const step = async () => normalizeStep({ available: false, unavailable: 'ANTHROPIC_API_KEY is not set. Add it to .env, or use GOAP-only mode.' })
    render(<ScenariosPanel client={base({ step })} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Step agent' }))
    expect(await screen.findByText(/ANTHROPIC_API_KEY is not set/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Policy verdict')).not.toBeInTheDocument()
  })

  it('shows backend errors and keeps working', async () => {
    const run = async () => { throw new Error('Issuing is not enabled') }
    render(<ScenariosPanel client={base({ run })} />)
    await userEvent.click((await screen.findAllByRole('button', { name: 'Run' }))[0])
    expect(await screen.findByRole('alert')).toHaveTextContent('Issuing is not enabled')
  })
})

describe('http client', () => {
  it('posts to the scenario and agent endpoints', async () => {
    const f = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ data: { status: 'completed', steps: [] } })))
    const c = createScenariosHttpClient('', f as unknown as typeof fetch)
    await c.run('delivery-pay', 'mock')
    expect(f.mock.calls[0][0]).toBe('/api/scenarios/delivery-pay/run')
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ mode: 'mock' })
    await c.step('m1')
    expect(f.mock.calls[1][0]).toBe('/api/agent/step')
  })
})
