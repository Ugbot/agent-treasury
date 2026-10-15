import type { Command, CommandResult, PolicyOption, Scenario, Snapshot } from '../types'

export interface TreasuryClient {
  kind: 'http' | 'fixture'
  getSnapshot(missionId: string): Promise<Snapshot>
  listScenarios(): Promise<Scenario[]>
  listPolicies(): Promise<PolicyOption[]>
  send(cmd: Command): Promise<CommandResult>
}
