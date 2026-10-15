import type { Command, CommandResult, PolicyOption, Scenario, Snapshot } from '../types'
import { ApiError } from './errors'
import type { TreasuryClient } from './client'

type Fetch = typeof fetch

async function unwrap<T>(res: Response): Promise<T> {
  let body: { data?: T; error?: { code: string; message: string; details?: unknown } } | null = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  if (!res.ok || !body || body.error || body.data === undefined) {
    throw new ApiError(res.status, body?.error ?? { code: 'bad_response', message: `Unexpected response (${res.status})` })
  }
  return body.data
}

/** Map a typed command onto the contract's resource endpoints. */
export function route(cmd: Command): { method: string; path: string; body: unknown } {
  const m = encodeURIComponent(cmd.missionId)
  const base = { commandId: cmd.commandId, expectedRevision: cmd.expectedRevision }
  switch (cmd.type) {
    case 'select_scenario':
      return { method: 'POST', path: '/api/missions', body: { commandId: cmd.commandId, ...cmd.payload } }
    case 'attach_policy': {
      const { targetType, targetId, policyId, policyVersion } = cmd.payload
      const seg = targetType === 'payment' ? 'payments' : targetType === 'card' ? 'cards' : targetType === 'counterparty' ? 'counterparties' : 'missions'
      const id = targetType === 'mission' ? m : encodeURIComponent(targetId)
      return { method: 'POST', path: `/api/${seg}/${id}/policy-attachments`, body: { ...base, policyId, policyVersion } }
    }
    default: {
      // Contract event names: position, cancel_requested; the rest pass through.
      const type = cmd.type === 'report_position' ? 'position' : cmd.type
      return {
        method: 'POST',
        path: `/api/missions/${m}/events`,
        body: { eventId: cmd.commandId, expectedRevision: cmd.expectedRevision, type, simTime: 0, payload: cmd.payload },
      }
    }
  }
}

export function createHttpClient(baseUrl = '', fetchImpl: Fetch = (...a) => fetch(...a)): TreasuryClient {
  const get = <T>(path: string) => fetchImpl(baseUrl + path, { headers: { accept: 'application/json' } }).then((r) => unwrap<T>(r))
  return {
    kind: 'http',
    getSnapshot: (missionId) => get<Snapshot>(`/api/state?missionId=${encodeURIComponent(missionId)}`),
    listScenarios: () => get<Scenario[]>('/api/scenarios'),
    // /api/policies is the policy registry router (array of bundles), not the {data} envelope.
    listPolicies: (): Promise<PolicyOption[]> =>
      fetchImpl(baseUrl + '/api/policies', { headers: { accept: 'application/json' } })
        .then((r) => r.json())
        .then((rows: unknown) =>
          Array.isArray(rows)
            ? rows.map((b: { policy_id?: string; id?: string; version: number; title?: string }) => ({
                policyId: b.policy_id ?? b.id ?? '',
                version: b.version,
                title: b.title ?? b.policy_id ?? b.id ?? '',
              }))
            : [],
        ),
    async send(cmd): Promise<CommandResult> {
      const r = route(cmd)
      const res = await fetchImpl(baseUrl + r.path, {
        method: r.method,
        headers: {
          'content-type': 'application/json', accept: 'application/json',
          ...(/approve|override/.test(String(cmd.type)) ? { 'X-Actor-Role': 'approver', 'X-Actor-Id': 'console-approver' } : {}),
        },
        body: JSON.stringify(r.body),
      })
      return unwrap<CommandResult>(res)
    },
  }
}
