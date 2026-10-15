import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Snapshot } from '../types'
import { DecisionPipeline } from './DecisionPipeline'
import { StatusHeadline } from './StatusHeadline'
import { describeStatus, pipelineStates } from './status'

const base: Snapshot = {
  revision: 1, mode: 'mock', missionId: 'm1', scenario: 'happy-path',
  inputs: { amountMinor: 2000, currency: 'usd', insured: true, location: 'depot', receiptConfirmed: false },
  policies: [{ policyId: 'org-base', version: 1, hash: 'h', mandatory: true }],
  decision: null, operation: null, events: [],
}
const clause = (clauseId: string, passed: boolean, prose = 'Some rule') => ({ clauseId, prose, passed, observed: passed ? 'ok' : '{"missing": "receipt_confirmed"}', expected: 'require_input' })
const withDecision = (verdict: 'allow' | 'hold' | 'deny', clauses: ReturnType<typeof clause>[], extra: Partial<Snapshot> = {}): Snapshot => ({
  ...base, ...extra,
  decision: { decisionId: 'd1', verdict, inputRevision: 1, effectivePolicyHash: 'abc123', clauses, obligations: [] },
})

describe('StatusHeadline wording', () => {
  it('held on receipt', () => {
    const s = withDecision('hold', [clause('PAY-1', false), clause('TRAVEL-1', true)])
    expect(describeStatus(s).text).toBe('Held: waiting for the customer to confirm receipt (rule PAY-1). $20.00 will be paid to the merchant and driver once it passes.')
  })
  it('denied for a prohibited zone says no money moves', () => {
    const s = withDecision('deny', [clause('TRAVEL-1', false)])
    expect(describeStatus(s).text).toBe('Denied: the route enters a prohibited zone (rule TRAVEL-1). No money moves.')
    expect(describeStatus(s).tone).toBe('deny')
  })
  it('paid once an operation exists', () => {
    const s = withDecision('allow', [clause('PAY-1', true)], {
      operation: { operationId: 'op1', kind: 'pay', amountMinor: 2000, currency: 'usd', status: 'succeeded', mode: 'mock', providerObjectId: null },
    })
    expect(describeStatus(s).text).toBe('Paid: $20.00 released to the merchant and driver. Every rule passed.')
  })
  it('mentions extra failing rules and unknown rules use their own prose', () => {
    const s = withDecision('hold', [clause('APPROVE-1', false), clause('X-9', false, 'Needs a second look')])
    expect(describeStatus(s).text).toContain('(rule APPROVE-1 and 1 more)')
    expect(describeStatus(withDecision('hold', [clause('X-9', false, 'Needs a second look')])).text).toContain('needs a second look (rule X-9)')
  })
  it('says what to do when there is no decision', () => {
    expect(describeStatus(base).text).toMatch(/Run a scenario/)
    expect(describeStatus(null).tone).toBe('idle')
  })
  it('renders as a status region', () => {
    render(<StatusHeadline s={withDecision('hold', [clause('PAY-1', false)])} />)
    expect(screen.getByRole('status')).toHaveTextContent(/^Held: waiting for the customer/)
  })
})

describe('pipeline states', () => {
  it('is idle without a mission or decision', () => {
    expect(pipelineStates(null)).toEqual({ inputs: 'idle', policies: 'idle', decision: 'idle', money: 'idle' })
    expect(pipelineStates(base)).toMatchObject({ policies: 'idle', decision: 'idle', money: 'idle' })
  })
  it('holds: inputs and policies wait, money waits', () => {
    expect(pipelineStates(withDecision('hold', [clause('PAY-1', false)]))).toEqual({ inputs: 'wait', policies: 'wait', decision: 'wait', money: 'wait' })
  })
  it('deny stops the pipeline before money', () => {
    const st = pipelineStates(withDecision('deny', [clause('TRAVEL-1', false)], { inputs: { ...base.inputs, receiptConfirmed: true, location: 'prohibited' } }))
    expect(st).toMatchObject({ policies: 'stop', decision: 'stop', money: 'stop' })
  })
  it('allow with an operation lights every stage', () => {
    const s = withDecision('allow', [clause('PAY-1', true)], {
      inputs: { ...base.inputs, receiptConfirmed: true },
      operation: { operationId: 'op1', kind: 'pay', amountMinor: 2000, currency: 'usd', status: 'succeeded', mode: 'stripe_test', providerObjectId: 'pi_123' },
    })
    expect(pipelineStates(s)).toEqual({ inputs: 'ok', policies: 'ok', decision: 'ok', money: 'ok' })
  })
})

describe('DecisionPipeline', () => {
  it('shows chained policies, clause ids with plain sentences, and the unmet input', () => {
    render(<DecisionPipeline s={withDecision('hold', [clause('PAY-1', false, 'Release only after delivery proof')])} />)
    expect(screen.getByText('Organisation base policy')).toBeInTheDocument()
    expect(screen.getByText('PAY-1')).toBeInTheDocument()
    expect(screen.getByText(/Release only after delivery proof/)).toBeInTheDocument()
    expect(screen.getByText('Hold')).toBeInTheDocument()
    expect(screen.getByText('Not paid yet.')).toBeInTheDocument()
    expect(screen.getByText('not confirmed')).toBeInTheDocument()
    expect(screen.getAllByText('simulated').length).toBeGreaterThan(0)
  })
  it('shows the Stripe object id and mode badge when money moved', () => {
    const s = withDecision('allow', [clause('PAY-1', true)], {
      mode: 'stripe_test',
      operation: { operationId: 'op1', kind: 'pay', amountMinor: 2000, currency: 'usd', status: 'succeeded', mode: 'stripe_test', providerObjectId: 'pi_123' },
    })
    render(<DecisionPipeline s={s} />)
    expect(screen.getByText('pi_123')).toBeInTheDocument()
    expect(screen.getByText('STRIPE TEST')).toBeInTheDocument()
  })
  it('says no money moved on deny', () => {
    render(<DecisionPipeline s={withDecision('deny', [clause('TRAVEL-1', false)])} />)
    expect(screen.getByText('No money moved.')).toBeInTheDocument()
  })
})
