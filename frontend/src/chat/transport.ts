import type { ChatResult, ChatTransport } from './types'

const SETUP = 'AI chat is unavailable. Set the model API key on the backend (see README) and restart.'

/** HTTP transport for POST /api/missions/{id}/chat/messages. Never holds keys. */
export function createHttpChatTransport(base = '', f: typeof fetch = fetch): ChatTransport {
  return {
    async send(missionId, req) {
      let res: Response
      try {
        res = await f(`${base}/api/missions/${encodeURIComponent(missionId)}/chat/messages`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(req),
        })
      } catch {
        return { ok: false, unavailable: true, message: 'Backend unreachable. ' + SETUP }
      }
      let body: unknown = null
      try {
        body = await res.json()
      } catch {
        /* fall through */
      }
      const b = body as Record<string, unknown> | null
      if (!res.ok || !b || b.unavailable || typeof b.text !== 'string') {
        const msg = typeof b?.message === 'string' ? b.message : (b?.error as { message?: string } | undefined)?.message
        return { ok: false, unavailable: true, message: msg ?? SETUP }
      }
      return { ok: true, reply: b as unknown as import('./types').ChatReply } satisfies ChatResult
    },
  }
}

/** Always-unavailable transport (fixture mode / no key). */
export const unavailableTransport: ChatTransport = {
  send: async () => ({ ok: false, unavailable: true, message: SETUP }),
}
