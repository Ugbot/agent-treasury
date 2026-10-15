import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import { createFixtureClient, createHttpClient } from '../api'
import { route } from '../api/http'
import * as cmds from '../api/commands'
import type { Command } from '../types'

const ctx = { missionId: 'm1', expectedRevision: 3 }

describe('typed commands', () => {
  it('typedInputCommands: unique ids, revision and integer minor units', () => {
    const a = cmds.setAmount(ctx, 6000)
    const b = cmds.setAmount(ctx, 6000)
    expect(a.commandId).not.toBe(b.commandId)
    expect(a).toMatchObject({ type: 'set_amount', missionId: 'm1', expectedRevision: 3, payload: { amountMinor: 6000, currency: 'usd' } })
    expect(cmds.setInsurance(ctx, false).payload).toEqual({ covered: false })
    expect(cmds.setLocation(ctx, 'depot').type).toBe('set_location')
    expect(cmds.confirmReceipt(ctx).type).toBe('confirm_receipt')
    expect(cmds.attachPolicy(ctx, 'p', 2).payload.policyVersion).toBe(2)
    expect(cmds.selectScenario(ctx, 's', 'mock').payload.mode).toBe('mock')
  })

  it('routes commands to contract endpoints', () => {
    expect(route(cmds.selectScenario(ctx, 's', 'mock')).path).toBe('/api/missions')
    expect(route(cmds.setAmount(ctx, 1)).path).toBe('/api/missions/m1/events')
    expect(route(cmds.attachPolicy(ctx, 'p', 1)).path).toBe('/api/payments/current/policy-attachments')
  })
})

describe('http client', () => {
  it('surfaces contract errors and stale revisions', async () => {
    const f = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'stale_revision', message: 'stale' } }), { status: 409 }))
    const c = createHttpClient('', f as unknown as typeof fetch)
    await expect(c.send(cmds.setAmount(ctx, 1) as Command)).rejects.toMatchObject({ code: 'stale_revision', isStale: true })
    expect(JSON.parse(f.mock.calls[0][1].body).eventId).toBeTruthy()
  })
  it('treats non-JSON as an error', async () => {
    const f = vi.fn().mockResolvedValue(new Response('boom', { status: 502 }))
    await expect(createHttpClient('', f as unknown as typeof fetch).getSnapshot('m')).rejects.toMatchObject({ code: 'bad_response' })
  })
})

describe('console', () => {
  it('moves from hold to deny to allow through backend-style decisions and labels fixture mode', async () => {
    const user = userEvent.setup()
    render(<App client={createFixtureClient()} />)
    expect(await screen.findByText(/Fixture mode: canned data/)).toBeInTheDocument()
    expect(await screen.findByText('Hold')).toBeInTheDocument()
    expect(screen.getByTestId('status-headline')).toHaveTextContent(/Held: waiting for the customer to confirm receipt \(rule PAY-1\)/)

    await user.click(screen.getByRole('button', { name: 'Confirm receipt' }))
    await waitFor(() => expect(screen.getByText('Allow')).toBeInTheDocument())
    expect(screen.getByText(/fixture paid/)).toBeInTheDocument()

    await user.type(screen.getByLabelText('Amount (USD)'), '75')
    await user.click(screen.getByRole('button', { name: 'Set amount' }))
    await waitFor(() => expect(screen.getByText('Hold')).toBeInTheDocument())
    expect(screen.getAllByText(/APPROVE-1/).length).toBeGreaterThan(0)

    await user.selectOptions(screen.getByLabelText('Location'), 'prohibited')
    await waitFor(() => expect(screen.getByText('Deny')).toBeInTheDocument())
    expect(screen.getAllByText('simulated').length).toBeGreaterThan(1)
    expect(screen.getAllByText('FIXTURE').length).toBeGreaterThan(1)
  })

  it('shows an error state and disables controls when backend is down', async () => {
    const client = createHttpClient('', (async () => { throw new Error('network down') }) as unknown as typeof fetch)
    render(<App client={client} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('network down')
    expect(screen.getByText(/Backend unavailable/)).toBeInTheDocument()
  })

  it('shows loading state', () => {
    const client = { ...createFixtureClient(), getSnapshot: () => new Promise<never>(() => {}) }
    render(<App client={client} />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading')
  })
})
