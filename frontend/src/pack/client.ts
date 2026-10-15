import { defaultFetch } from '../lib/wire'
import type { FetchImpl } from '../lib/wire'

const demoAuth = { 'X-Party-Role': 'admin', 'X-Party-Id': 'console' }

/** Fetch the audit pack zip for a mission (demo auth headers; server enforces admin/approver). */
export async function fetchAuditPack(mission: string, f: FetchImpl = defaultFetch): Promise<Blob> {
  const res = await f(`/api/audit/pack?mission=${encodeURIComponent(mission)}`, { headers: demoAuth })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try { msg = (await res.json())?.error?.message ?? msg } catch { /* not json */ }
    throw new Error(msg)
  }
  return res.blob()
}
