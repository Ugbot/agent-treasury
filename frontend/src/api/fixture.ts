import type { ClauseResult, Command, CommandResult, Decision, FeedEvent, Inputs, Snapshot } from '../types'
import { ApiError } from './errors'
import type { TreasuryClient } from './client'

const POLICIES = [
  { policyId: 'org-base', version: 1, title: 'Organisation base policy' },
  { policyId: 'travel-insured', version: 2, title: 'Insured travel only' },
]
const SCENARIOS = [
  { id: 'delivery-happy', title: 'Delivery with receipt' },
  { id: 'prohibited-zone', title: 'Route enters prohibited zone' },
]

/**
 * Explicit fixture mode: canned, in-memory data for component development.
 * Everything it produces is labelled mode 'fixture'. It has no payment authority.
 */
export function createFixtureClient(): TreasuryClient {
  let revision = 1
  let simTime = 0
  let inputs: Inputs = { amountMinor: 4200, currency: 'usd', insured: true, location: 'depot', receiptConfirmed: false }
  let scenario = SCENARIOS[0].id
  let attached = [{ policyId: 'org-base', version: 1, hash: 'fixture0001', mandatory: true }]
  const events: FeedEvent[] = [
    { eventId: 'e0', type: 'mission_created', simTime: 0, description: 'Fixture mission created', mode: 'fixture', source: 'simulation' },
  ]
  const seen = new Map<string, CommandResult>()

  const decide = (): Decision => {
    const clauses: ClauseResult[] = [
      { clauseId: 'PAY-1', prose: 'Release only after delivery proof', passed: inputs.receiptConfirmed, observed: String(inputs.receiptConfirmed), expected: 'true' },
      { clauseId: 'TRAVEL-1', prose: 'No payment through a prohibited zone', passed: inputs.location !== 'prohibited', observed: inputs.location, expected: 'not prohibited' },
      { clauseId: 'APPROVE-1', prose: 'Amounts above GBP 50.00 need approval', passed: inputs.amountMinor <= 5000, observed: String(inputs.amountMinor), expected: '<= 5000' },
    ]
    if (attached.some((p) => p.policyId === 'travel-insured')) {
      clauses.push({ clauseId: 'INS-1', prose: 'Driver must be insured', passed: inputs.insured, observed: String(inputs.insured), expected: 'true' })
    }
    const deny = !clauses[1].passed
    const failed = clauses.filter((c) => !c.passed)
    const verdict = deny ? 'deny' : failed.length ? 'hold' : 'allow'
    return {
      decisionId: `fx-dec-${revision}`,
      verdict,
      inputRevision: revision,
      effectivePolicyHash: attached.map((p) => p.hash).join('+'),
      clauses,
      obligations: failed.filter((c) => c.clauseId !== 'TRAVEL-1').map((c) => ({ obligationId: `ob-${c.clauseId}`, kind: c.clauseId, status: 'pending' })),
    }
  }

  const snapshot = (): Snapshot => {
    const decision = decide()
    return {
      revision,
      mode: 'fixture',
      missionId: 'fixture-mission',
      scenario,
      inputs,
      policies: attached,
      decision,
      operation:
        decision.verdict === 'allow'
          ? { operationId: 'fx-op-1', kind: 'transfer', amountMinor: inputs.amountMinor, currency: 'usd', status: 'fixture_paid', mode: 'fixture', providerObjectId: null }
          : null,
      events: [...events],
    }
  }

  return {
    kind: 'fixture',
    async getSnapshot() {
      return snapshot()
    },
    async listScenarios() {
      return SCENARIOS
    },
    async listPolicies() {
      return POLICIES
    },
    async send(cmd: Command) {
      const prior = seen.get(cmd.commandId)
      if (prior) return prior
      if (cmd.expectedRevision !== revision) {
        throw new ApiError(409, { code: 'stale_revision', message: `Expected revision ${cmd.expectedRevision}, current is ${revision}` })
      }
      let description = ''
      switch (cmd.type) {
        case 'set_amount':
          inputs = { ...inputs, amountMinor: cmd.payload.amountMinor }
          description = `Amount set to ${cmd.payload.amountMinor} minor units`
          break
        case 'set_insurance':
          inputs = { ...inputs, insured: cmd.payload.covered }
          description = `Insurance ${cmd.payload.covered ? 'covered' : 'not covered'}`
          break
        case 'set_location':
          inputs = { ...inputs, location: cmd.payload.location }
          description = `Location set to ${cmd.payload.location}`
          break
        case 'confirm_receipt':
          inputs = { ...inputs, receiptConfirmed: true }
          description = 'Receipt confirmed'
          break
        case 'attach_policy': {
          const p = POLICIES.find((x) => x.policyId === cmd.payload.policyId)
          if (!p) throw new ApiError(422, { code: 'unknown_policy', message: 'Unknown policy' })
          if (!attached.some((a) => a.policyId === p.policyId)) {
            attached = [...attached, { policyId: p.policyId, version: p.version, hash: `fixture${p.version}`, mandatory: false }]
          }
          description = `Policy ${p.policyId} v${p.version} attached`
          break
        }
        case 'report_position':
          description = `Driver position reported (${cmd.payload.x}, ${cmd.payload.y})`
          break
        case 'cancel_requested':
          description = `Cancel requested: ${cmd.payload.reason}`
          break
        case 'select_scenario':
          scenario = cmd.payload.scenario
          inputs = { ...inputs, location: scenario === 'prohibited-zone' ? 'prohibited' : 'depot', receiptConfirmed: false }
          description = `Scenario ${scenario} selected`
          break
      }
      revision += 1
      simTime += 1
      events.push({ eventId: `e${revision}`, type: cmd.type, simTime, description, mode: 'fixture', source: 'simulation' })
      const result = { accepted: true, revision, commandId: cmd.commandId }
      seen.set(cmd.commandId, result)
      return result
    },
  }
}
