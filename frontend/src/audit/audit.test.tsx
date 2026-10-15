import { act, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { createAuditHttpClient, normalizeEntry } from './client'
import { AuditLogView } from './AuditLogView'
import { useAuditFeed } from './useAuditFeed'
import type { AuditClient, AuditEntry } from './types'

const entry = (seq: number, e: Partial<AuditEntry> = {}): AuditEntry => ({
  seq, ts: '2026-10-15T18:00:00+00:00', actor: 'system', party: null, mission_id: 'm1', kind: 'input.event', summary: `s${seq}`,
  payload: {}, policy: null, stripe: null, mode: 'mock', prev_hash: 'p', hash: 'h', ...e,
})

function fake(over: Partial<AuditClient> = {}): AuditClient & { rows: AuditEntry[] } {
  const rows: AuditEntry[] = [entry(1), entry(2)]
  return {
    rows,
    kind: 'http',
    list: vi.fn(async (q = {}) => rows.filter((r) => r.seq > (q.after ?? 0))),
    party: vi.fn(async (p: string, after = 0) => rows.filter((r) => r.seq > after && r.party === p)),
    verify: vi.fn(async () => ({ ok: true, entries: rows.length })),
    openStream: () => null,
    ...over,
  }
}

describe('normalizeEntry', () => {
  it('rejects rows without a seq and fills defaults', () => {
    expect(normalizeEntry({ kind: 'x' })).toBeNull()
    expect(normalizeEntry({ seq: 3, kind: 'decision', policy: { verdict: 'hold' } })).toMatchObject({ seq: 3, actor: 'system', payload: {}, policy: { verdict: 'hold' }, stripe: null })
  })
})

describe('http client', () => {
  it('reads the party-scoped endpoint with after=seq and unwraps the envelope', async () => {
    const f = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: [{ seq: 5, kind: 'k' }] }), { status: 200 }))
    const c = createAuditHttpClient('/api/audit', f as unknown as typeof fetch)
    const rows = await c.party('driver', 4)
    expect(f.mock.calls[0][0]).toBe('/api/audit/party/driver?after=4')
    expect(rows.map((r) => r.seq)).toEqual([5])
  })
  it('verify reports a broken chain', async () => {
    const f = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: false, broken_at_seq: 7, entries: 9 })))
    const c = createAuditHttpClient('/api/audit', f as unknown as typeof fetch)
    expect(await c.verify()).toMatchObject({ ok: false, broken_at_seq: 7 })
  })
})

describe('useAuditFeed', () => {
  it('falls back to polling with after=seq when SSE is unavailable, without duplicates', async () => {
    const c = fake()
    const { result } = renderHook(() => useAuditFeed(c, { pollMs: 15 }))
    await waitFor(() => expect(result.current.entries).toHaveLength(2))
    await waitFor(() => expect(result.current.transport).toBe('polling'))
    c.rows.push(entry(3))
    await waitFor(() => expect(result.current.entries.map((e) => e.seq)).toEqual([1, 2, 3]))
    expect(vi.mocked(c.list).mock.calls.some(([q]) => q?.after === 2)).toBe(true)
  })

  it('uses the stream, dedupes overlap, and falls back to polling on stream error', async () => {
    let push: (e: AuditEntry[]) => void = () => {}
    let fail: () => void = () => {}
    const c = fake({ openStream: (_q, on, err) => { push = on; fail = err; return () => {} } })
    const { result } = renderHook(() => useAuditFeed(c, { party: 'driver', pollMs: 15 }))
    await waitFor(() => expect(result.current.transport).toBe('sse'))
    act(() => push([entry(1, { party: 'driver' }), entry(1, { party: 'driver' })]))
    expect(result.current.entries).toHaveLength(1)
    act(() => fail())
    await waitFor(() => expect(result.current.transport).toBe('polling'))
    expect(c.party).toHaveBeenCalledWith('driver', 0)
  })
})

describe('AuditLogView', () => {
  const rows = [
    entry(1, { actor: 'agent', kind: 'agent.proposal', party: null, mode: 'mock' }),
    entry(2, { actor: 'system', kind: 'decision', party: 'sender', mode: 'stripe_test', policy: { verdict: 'hold' } }),
    entry(3, { actor: 'receiver', kind: 'delivery.proof', party: 'receiver', mode: 'stripe_test', payload: { payment_id: 'pay-1' } }),
  ]
  it('filters by party, kind, actor, verdict and mode', async () => {
    const c = fake()
    c.rows.splice(0, c.rows.length, ...rows)
    render(<AuditLogView client={c} pollMs={50} />)
    await screen.findByText('s3')
    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('Verdict'), 'hold')
    expect(screen.queryByText('s1')).not.toBeInTheDocument()
    expect(screen.getByText('s2')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear' }))
    await user.selectOptions(screen.getByLabelText('Mode'), 'mock')
    expect(screen.getByText('s1')).toBeInTheDocument()
    expect(screen.queryByText('s3')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear' }))
    await user.selectOptions(screen.getByLabelText('Party'), 'receiver')
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    await user.selectOptions(screen.getByLabelText('Actor'), 'receiver')
    await user.selectOptions(screen.getByLabelText('Kind'), 'delivery.proof')
    expect(screen.getByText('s3')).toBeInTheDocument()
  })

  it('shows verified and broken badges, and a Why link for payment entries', async () => {
    const c = fake()
    c.rows.splice(0, c.rows.length, ...rows)
    const onWhy = vi.fn()
    const { unmount } = render(<AuditLogView client={c} onWhy={onWhy} pollMs={50} />)
    expect(await screen.findByText(/Hash chain verified, 3 entries/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Why was this paid?' }))
    expect(onWhy).toHaveBeenCalledWith('pay-1', 3)
    unmount()
    const bad = fake({ verify: async () => ({ ok: false, entries: 3, broken_at_seq: 2 }) })
    render(<AuditLogView client={bad} pollMs={50} />)
    expect(await screen.findByText(/CHAIN BROKEN at entry #2/)).toBeInTheDocument()
    expect(within(screen.getByTestId('chain-badge')).getByRole('button', { name: 'Re-verify' })).toBeInTheDocument()
  })

  it('labels mock entries SIMULATED and does not claim verification when the endpoint fails', async () => {
    const c = fake({ verify: async () => { throw new Error('down') } })
    render(<AuditLogView client={c} pollMs={50} />)
    expect((await screen.findAllByText(/MOCK · SIMULATED/)).length).toBeGreaterThan(0)
    expect(await screen.findByText(/Chain not verified/)).toBeInTheDocument()
  })
})
