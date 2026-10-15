import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { MemoryPanel } from './MemoryPanel'
import type { MemoryClient } from './client'

const mk = (source: string, ready = true): MemoryClient => ({
  kind: 'http',
  status: async () => ({ ready, mode: 'local', reachable: ready, message: 'x', last_write: { ref: 'cognee://a/b' } }),
  explain: async (question) => ({ source, question, clause_ids: ['TRAVEL-1'], evidence: ['Blocked.'], policy_id: 'p', policy_version: 1 }),
  sync: async () => ({ written: 3 }),
})

describe('MemoryPanel', () => {
  it('shows connected status and cognee-sourced answer', async () => {
    render(<MemoryPanel client={mk('cognee')} />)
    expect(await screen.findByText('Connected, local mode')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Is there a rule that freezes/ }))
    expect(await screen.findByText('Answered from Cognee memory')).toBeInTheDocument()
    expect(screen.getByText('TRAVEL-1')).toBeInTheDocument()
  })
  it('never claims Cognee for audit log answers, and syncs', async () => {
    render(<MemoryPanel client={mk('audit_log', false)} />)
    expect(await screen.findByText(/Not connected/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Ask the policy memory'), 'hi')
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByText(/Answered from the audit log/)).toBeInTheDocument()
    expect(screen.queryByText('Answered from Cognee memory')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /Sync decisions/ }))
    expect(await screen.findByText('Wrote 3 decisions to Cognee.')).toBeInTheDocument()
  })
})
