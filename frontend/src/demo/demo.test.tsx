import { describe, expect, it } from 'vitest'
import { DEMO_STEPS, MAX_TOTAL_SECONDS, freshRunId, planSteps, stepAt, stepStart, totalSeconds } from './script'

describe('demo script', () => {
  it('fits in 110 seconds with and without the optional freeze step', () => {
    expect(totalSeconds()).toBeLessThanOrEqual(MAX_TOTAL_SECONDS)
    expect(totalSeconds(planSteps(false))).toBeLessThanOrEqual(MAX_TOTAL_SECONDS)
  })
  it('keeps the story order and drops freeze when unavailable', () => {
    expect(DEMO_STEPS.map((s) => s.id)).toEqual(['policies', 'deliver', 'replan', 'refund', 'freeze', 'why'])
    expect(planSteps(false).map((s) => s.id)).toEqual(['policies', 'deliver', 'replan', 'refund', 'why'])
    expect(planSteps(true)).toHaveLength(6)
  })
  it('sequences by elapsed time', () => {
    const s = planSteps(true)
    expect(stepAt(s, 0)?.index).toBe(0)
    expect(stepAt(s, stepStart(s, 3))?.index).toBe(3)
    expect(stepAt(s, totalSeconds(s))).toBeNull()
    expect(s.every((x) => x.caption.length > 0 && x.seconds > 0)).toBe(true)
  })
  it('uses a fresh run id each time', () => {
    expect(freshRunId(1, 0.1)).not.toBe(freshRunId(1, 0.2))
  })
})
