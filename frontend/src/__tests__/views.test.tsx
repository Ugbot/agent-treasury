import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from '../App'
import { createFixtureClient } from '../api'

describe('App tabs in fixture mode', () => {
  it('opens the why drawer from the header, then jumps to the audit entry in the Everything log', async () => {
    const user = userEvent.setup()
    render(<App client={createFixtureClient()} />)
    await screen.findByText(/Mission fixture-mission/)
    // fixture starts held (no receipt): why is disabled until a payment exists
    expect(screen.getByRole('button', { name: 'Why was this paid?' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Everything log' }))
    expect((await screen.findAllByText('Receiver confirmed receipt')).length).toBeGreaterThan(0)
    expect(screen.getByTestId('chain-badge')).toHaveTextContent('(fixture data)')
    expect(screen.getAllByText(/FIXTURE · SIMULATED/).length).toBeGreaterThan(0)
  })

  it('shows scenarios and the parties lens on one screen', async () => {
    const user = userEvent.setup()
    render(<App client={createFixtureClient()} />)
    await screen.findByText(/Mission fixture-mission/)
    expect(await screen.findAllByRole('button', { name: /^Run / })).toHaveLength(4)
    await user.click(screen.getByRole('button', { name: 'All three side by side' }))
    expect(await screen.findByRole('region', { name: 'Receiver' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Sender' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Driver' })).toBeInTheDocument()
  })
})
