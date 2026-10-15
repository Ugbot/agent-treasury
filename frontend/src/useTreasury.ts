import { useCallback, useEffect, useRef, useState } from 'react'
import type { Ctx, TreasuryClient } from './api'
import { ApiError } from './api'
import type { Command, PolicyOption, Scenario, Snapshot } from './types'

export interface TreasuryState {
  snapshot: Snapshot | null
  scenarios: Scenario[]
  policies: PolicyOption[]
  loading: boolean
  pending: boolean
  error: string | null
  ctx: Ctx | null
  refresh: () => Promise<void>
  run: (build: (ctx: Ctx) => Command) => Promise<void>
}

export function useTreasury(client: TreasuryClient, missionId = 'current'): TreasuryState {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [policies, setPolicies] = useState<PolicyOption[]>([])
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const latest = useRef(-1)

  const accept = useCallback((s: Snapshot) => {
    // Render accepted snapshots by monotonically increasing revision only.
    if (s.revision >= latest.current) {
      latest.current = s.revision
      setSnapshot(s)
    }
  }, [])

  const refresh = useCallback(async () => {
    try {
      accept(await client.getSnapshot(missionId))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed')
    }
  }, [client, missionId, accept])

  useEffect(() => {
    let live = true
    setLoading(true)
    ;(async () => {
      await refresh()
      try {
        const [sc, po] = await Promise.all([client.listScenarios(), client.listPolicies()])
        if (live) {
          setScenarios(sc)
          setPolicies(po)
        }
      } catch {
        /* optional lists; deck shows disabled controls */
      }
      if (live) setLoading(false)
    })()
    return () => {
      live = false
    }
  }, [client, refresh])

  const run = useCallback(
    async (build: (ctx: Ctx) => Command) => {
      if (!snapshot) return
      setPending(true)
      try {
        await client.send(build({ missionId: snapshot.missionId, expectedRevision: snapshot.revision, currency: snapshot.inputs.currency }))
        setError(null)
      } catch (e) {
        setError(e instanceof ApiError && e.isStale ? 'State changed elsewhere; refreshed. Try again.' : e instanceof Error ? e.message : 'Command failed')
      } finally {
        await refresh() // never paint money as confirmed optimistically
        setPending(false)
      }
    },
    [client, snapshot, refresh],
  )

  const ctx = snapshot ? { missionId: snapshot.missionId, expectedRevision: snapshot.revision, currency: snapshot.inputs.currency } : null
  return { snapshot, scenarios, policies, loading, pending, error, ctx, refresh, run }
}
