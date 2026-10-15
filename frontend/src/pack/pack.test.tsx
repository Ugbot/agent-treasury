import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { fetchAuditPack } from './client'
import { PackButton } from './PackButton'

describe('audit pack', () => {
  it('requests the pack with auth headers', async () => {
    const f = vi.fn(async () => new Response(new Blob(['zip']), { status: 200 }))
    await fetchAuditPack('m 1', f as never)
    expect(f).toHaveBeenCalledWith('/api/audit/pack?mission=m%201', { headers: expect.objectContaining({ 'X-Party-Role': 'admin' }) })
  })
  it('shows the server error', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ error: { message: 'nope' } }), { status: 403 }))
    render(<PackButton mission="m1" fetchImpl={f as never} />)
    await userEvent.click(screen.getByRole('button', { name: /download audit pack/i }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/nope/))
  })
})
