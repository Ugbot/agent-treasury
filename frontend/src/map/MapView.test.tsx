import { act, render, screen } from '@testing-library/react'
import { fireEvent } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MapView } from './MapView'
import { pointAt } from './geometry'

afterEach(() => vi.useRealTimers())

describe('MapView', () => {
  it('labels simulated, shows legend and text equivalents', () => {
    render(<MapView />)
    expect(screen.getByTestId('sim-label')).toHaveTextContent('SIMULATED')
    expect(screen.getByRole('list', { name: 'Legend' })).toBeInTheDocument()
    expect(screen.getByRole('img')).toHaveAccessibleDescription(/Simulated\. Driver Driver A/)
    expect(screen.getByLabelText('Failed clauses')).toHaveTextContent('crosses Prohibited zone')
    expect(screen.getByLabelText('Replacement plan')).toHaveTextContent('Use route r-new')
    expect(screen.getByText(/Not a Stripe transaction/)).toBeInTheDocument()
  })

  it('movementEventsAndReplay: step and run emit position events only; scrub emits nothing', async () => {
    vi.useFakeTimers()
    const onPositionEvent = vi.fn()
    render(<MapView onPositionEvent={onPositionEvent} tickMs={100} steps={4} />)
    fireEvent.click(screen.getByText('Step'))
    expect(onPositionEvent).toHaveBeenCalledTimes(1)
    const e = onPositionEvent.mock.calls[0][0]
    expect(Object.keys(e).sort()).toEqual(['assignmentVersion', 'driverId', 'simTime', 'source', 'type', 'x', 'y'])
    expect(e).toMatchObject({ type: 'position', driverId: 'd1', source: 'simulation', simTime: 1 })
    fireEvent.click(screen.getByText('Run'))
    act(() => { vi.advanceTimersByTime(200) })
    expect(onPositionEvent).toHaveBeenCalledTimes(3)
    fireEvent.click(screen.getByText('Pause'))
    act(() => { vi.advanceTimersByTime(500) })
    expect(onPositionEvent).toHaveBeenCalledTimes(3)
    fireEvent.change(screen.getByLabelText('Scrub history'), { target: { value: '1' } })
    expect(onPositionEvent).toHaveBeenCalledTimes(3)
    expect(screen.getByRole('status')).toHaveTextContent('Replaying history: no events sent')
  })

  it('has no payment controls', () => {
    render(<MapView mode="stripe_test" />)
    expect(screen.queryByRole('button', { name: /pay|refund|capture|transfer|authori[sz]e/i })).toBeNull()
    expect(screen.getByText(/SIMULATED coordinates/)).toBeInTheDocument()
  })

  it('pointAt interpolates and clamps', () => {
    const line = [{ x: 0, y: 0 }, { x: 10, y: 0 }]
    expect(pointAt(line, 0.5)).toEqual({ x: 5, y: 0 })
    expect(pointAt(line, 2)).toEqual({ x: 10, y: 0 })
    expect(pointAt(line, NaN)).toEqual({ x: 0, y: 0 })
  })
})
