import { ApiError } from '../api/errors'

type Fetch = typeof fetch
export type FetchImpl = Fetch
export const defaultFetch: Fetch = (...a) => fetch(...a)

/**
 * Accepts either the console envelope ({data} / {error}) or a bare FastAPI body ({detail} on error).
 * Anything else is rejected rather than guessed at.
 */
export async function readWire<T>(res: Response): Promise<T> {
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  const o = (body && typeof body === 'object' ? body : null) as Record<string, unknown> | null
  if (!res.ok) {
    const e = o?.error as { code?: string; message?: string; details?: unknown } | undefined
    const detail = o?.detail
    throw new ApiError(res.status, {
      code: e?.code ?? (res.status === 503 ? 'unavailable' : 'http_error'),
      message: e?.message ?? (typeof detail === 'string' ? detail : `Request failed (${res.status})`),
      details: e?.details ?? detail,
    })
  }
  if (o && 'error' in o && o.error) {
    const e = o.error as { code?: string; message?: string }
    throw new ApiError(res.status, { code: e.code ?? 'error', message: e.message ?? 'Request failed' })
  }
  if (body === null) throw new ApiError(res.status, { code: 'bad_response', message: 'Unexpected response' })
  return (o && 'data' in o ? o.data : body) as T
}

export const jsonInit = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { 'content-type': 'application/json', accept: 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
})

export const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

const r = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {})
export const rec = r
/** Read a field that may be snake_case or camelCase. */
export function pick(o: unknown, ...keys: string[]): unknown {
  const x = r(o)
  for (const k of keys) if (x[k] !== undefined && x[k] !== null) return x[k]
  return undefined
}
export const str = (v: unknown): string | undefined => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : undefined)
export const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)
export const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])

/** Integer minor units to a currency string; the currency comes from the data (policy CURRENCY-1), never assumed. */
export function formatMoney(minor: number, currency: string): string {
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: currency.toUpperCase() }).format(minor / 100)
  } catch {
    return `${(minor / 100).toFixed(2)} ${currency.toUpperCase()}`
  }
}
