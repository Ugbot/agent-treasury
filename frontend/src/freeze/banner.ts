import type { FreezeState } from './types'

export function frozenText(f: FreezeState): string {
  const who = f.by ?? 'an approver'
  const when = f.since ? new Date(f.since).toLocaleString() : 'now'
  return `Payments are frozen by ${who} since ${when}: every payment is held`
}
