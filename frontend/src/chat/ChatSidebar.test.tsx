import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Decision } from '../types'
import { ChatSidebar } from './ChatSidebar'
import { createHttpChatTransport } from './transport'
import type { ChatTransport } from './types'

const decision: Decision = {
  decisionId: 'd1', verdict: 'hold', inputRevision: 1, effectivePolicyHash: 'h', obligations: [],
  clauses: [{ clauseId: 'c1', prose: 'Receipt required', passed: false, observed: 'none', expected: 'confirmed' }],
}

const okTransport = (extra = {}): ChatTransport => ({
  send: async (_m, _r, onDelta) => {
    onDelta?.('Hel')
    return { ok: true, reply: { messageId: 'r1', text: 'Held for receipt', provider: 'anthropic', model: 'claude', citations: [{ policyId: 'p', version: 1, clauseId: 'c1', evidenceIds: ['e1'], decisionId: 'd1' }], ...extra } }
  },
})

describe('ChatSidebar', () => {
  it('shows reply with decision-linked explanation', async () => {
    render(<ChatSidebar missionId="m" decision={decision} transport={okTransport()} />)
    await userEvent.type(screen.getByLabelText(/ask about/i), 'why?')
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByText('Held for receipt')).toBeInTheDocument()
    expect(screen.getByTestId('citation')).toHaveTextContent('failed: Receipt required')
    expect(screen.getByText(/anthropic \/ claude/)).toBeInTheDocument()
  })

  it('flags citations for a stale decision', async () => {
    render(<ChatSidebar missionId="m" decision={{ ...decision, decisionId: 'd2' }} transport={okTransport()} />)
    await userEvent.type(screen.getByLabelText(/ask about/i), 'why?')
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByText(/not the current decision/)).toBeInTheDocument()
  })

  it('streams deltas into the pending message', async () => {
    let release: () => void = () => {}
    const t: ChatTransport = {
      send: (_m, _r, onDelta) => new Promise((res) => {
        onDelta?.('Part')
        release = () => res({ ok: true, reply: { messageId: 'r', text: 'Part done', provider: 'p', model: 'm', citations: [] } })
      }),
    }
    render(<ChatSidebar missionId="m" decision={null} transport={t} />)
    await userEvent.type(screen.getByLabelText(/ask about/i), 'hi')
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByText('Part')).toBeInTheDocument()
    release()
    expect(await screen.findByText('Part done')).toBeInTheDocument()
  })

  it('draft is editable and only delivered on explicit send', async () => {
    const onSend = vi.fn()
    render(<ChatSidebar missionId="m" decision={decision} transport={okTransport({ draftId: 'dr1', draftText: 'Hello driver' })} onSendDraft={onSend} />)
    await userEvent.type(screen.getByLabelText(/ask about/i), 'draft')
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
    const box = await screen.findByLabelText('Draft message')
    expect(onSend).not.toHaveBeenCalled()
    await userEvent.clear(box)
    await userEvent.type(box, 'Edited')
    await userEvent.click(screen.getByRole('button', { name: 'Send draft' }))
    expect(onSend).toHaveBeenCalledWith('dr1', 'Edited')
    expect(screen.getByRole('status')).toHaveTextContent('sent locally')
  })

  it('unavailable state disables input with setup message', async () => {
    render(<ChatSidebar missionId="m" decision={null} />)
    await userEvent.type(screen.getByLabelText(/ask about/i), 'hi')
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByTestId('chat-unavailable')).toHaveTextContent(/API key/)
    expect(screen.getByLabelText(/ask about/i)).toBeDisabled()
  })
})

describe('http chat transport', () => {
  it('maps failures to unavailable', async () => {
    const f = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { message: 'no key' } }), { status: 503 }))
    const r = await createHttpChatTransport('', f as unknown as typeof fetch).send('m', { commandId: 'c', text: 't', historyIds: [] })
    expect(r).toEqual({ ok: false, unavailable: true, message: 'no key' })
    await waitFor(() => expect(f).toHaveBeenCalledOnce())
  })
})
