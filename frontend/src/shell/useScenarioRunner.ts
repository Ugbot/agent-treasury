import { useCallback, useEffect, useState } from 'react'
import { errMsg } from '../lib/wire'
import type { AgentStep, RunMode, RunResult, ScenarioInfo, ScenariosClient } from '../scenarios/types'

export interface ScenarioRunner {
  list: ScenarioInfo[]
  mode: RunMode
  setMode: (m: RunMode) => void
  busy: string | null
  error: string | null
  lastRun: RunResult | null
  steps: AgentStep[]
  run: (s: ScenarioInfo) => Promise<void>
  step: () => Promise<void>
}

/** Thin state wrapper over the existing ScenariosClient. No new data logic. */
export function useScenarioRunner(client: ScenariosClient, missionId: string | undefined, onRan?: (r: RunResult) => void): ScenarioRunner {
  const [list, setList] = useState<ScenarioInfo[]>([])
  const [mode, setMode] = useState<RunMode>('mock')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastRun, setLastRun] = useState<RunResult | null>(null)
  const [steps, setSteps] = useState<AgentStep[]>([])
  const [mission, setMission] = useState<string | undefined>(missionId)

  useEffect(() => {
    let live = true
    client.list().then((l) => live && setList(l), (e) => live && setError(`Could not load scenarios: ${errMsg(e)}`))
    return () => { live = false }
  }, [client])
  useEffect(() => { if (missionId) setMission(missionId) }, [missionId])

  const act = useCallback(async <T,>(key: string, fn: () => Promise<T>, done: (t: T) => void) => {
    setBusy(key)
    setError(null)
    try { done(await fn()) } catch (e) { setError(errMsg(e)) } finally { setBusy(null) }
  }, [])

  const run = useCallback((s: ScenarioInfo) =>
    act(`run:${s.id}`, () => client.run(s.id, mode), (r) => {
      setLastRun(r)
      if (r.missionId) setMission(r.missionId)
      setSteps([])
      onRan?.(r)
    }), [act, client, mode, onRan])
  const step = useCallback(() => act('step', () => client.step(mission ?? 'current'), (s) => setSteps((c) => [s, ...c])), [act, client, mission])

  return { list, mode, setMode, busy, error, lastRun, steps, run, step }
}
