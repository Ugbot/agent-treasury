export interface ScenarioInfo {
  id: string
  title: string
}

export type RunMode = 'mock' | 'stripe_test'

export interface RunStep {
  name: string
  status: string
  detail: string | null
}

export interface RunResult {
  scenarioId: string
  missionId: string | null
  status: string
  mode: string | null
  steps: RunStep[]
  /** Set when the backend reports a capability as unavailable (missing key, Issuing off, ...). */
  unavailable: string | null
}

export interface AgentProposal {
  action: string
  summary: string | null
  amountMinor: number | null
  currency: string | null
  recipient: string | null
  rationale: string | null
  agent: string
}

export interface AgentVerdict {
  verdict: string | null
  executed: boolean | null
  reasons: string[]
  clauses: { clauseId: string; outcome: string | null; detail: string | null }[]
}

export interface AgentStep {
  available: boolean
  unavailable: string | null
  mode: string | null
  proposal: AgentProposal | null
  verdict: AgentVerdict | null
  auditSeq: number | null
}

export interface ScenariosClient {
  kind: 'http' | 'fixture'
  list(): Promise<ScenarioInfo[]>
  run(id: string, mode: RunMode): Promise<RunResult>
  step(missionId: string, agent?: 'auto' | 'llm' | 'goap'): Promise<AgentStep>
}
