import type { MapClause, MapPlan, MapState } from './types'

/** Canned map for fixture mode. Not backend state and not a payment. */
export const FIXTURE_MAP: MapState = {
  merchant: { placeId: 'merchant', label: 'Merchant', position: { x: 10, y: 80 }, radiusMetres: 100 },
  destination: { placeId: 'dest', label: 'Recipient', position: { x: 90, y: 20 }, radiusMetres: 150 },
  drivers: [
    { driverId: 'd1', name: 'Driver A', position: { x: 10, y: 80 }, assignmentVersion: 1, eligible: true, coverageRecordId: 'cov-1' },
    { driverId: 'd2', name: 'Driver B', position: { x: 30, y: 90 }, assignmentVersion: 1, eligible: false, coverageRecordId: 'cov-2' },
  ],
  zones: [
    { zoneId: 'z-ok', label: 'Insured delivery area', permitted: true, polygon: [{ x: 0, y: 60 }, { x: 70, y: 60 }, { x: 100, y: 0 }, { x: 40, y: 0 }, { x: 0, y: 40 }] },
    { zoneId: 'z-no', label: 'Prohibited zone', permitted: false, polygon: [{ x: 55, y: 65 }, { x: 80, y: 65 }, { x: 80, y: 95 }, { x: 55, y: 95 }] },
  ],
  fuelStops: [
    { placeId: 'f1', label: 'Fuel stop 1', position: { x: 40, y: 55 }, radiusMetres: 80 },
    { placeId: 'f2', label: 'Fuel stop 2', position: { x: 70, y: 80 }, radiusMetres: 80 },
  ],
  routes: [
    { routeId: 'r-old', status: 'replaced', zoneIds: ['z-no'], points: [{ x: 10, y: 80 }, { x: 65, y: 80 }, { x: 90, y: 20 }] },
    { routeId: 'r-new', status: 'active', zoneIds: ['z-ok'], points: [{ x: 10, y: 80 }, { x: 40, y: 55 }, { x: 60, y: 35 }, { x: 90, y: 20 }] },
  ],
}
export const FIXTURE_CLAUSES: MapClause[] = [
  { clauseId: 'route-avoids-prohibited', prose: 'Route avoids prohibited zones', passed: false, observed: 'crosses Prohibited zone', expected: 'no crossing' },
  { clauseId: 'driver-insured', prose: 'Assigned driver has coverage', passed: true },
]
export const FIXTURE_PLAN: MapPlan = { planId: 'plan-2', replaces: 'r-old', actions: ['Use route r-new', 'Re-verify coverage'], reason: 'Original route crossed a prohibited zone' }
