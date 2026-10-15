/** Map shapes from docs/API_CONTRACT.md (Snapshot.map). Coordinates are simulated plane units 0-100. */
export interface Pt { x: number; y: number }
export interface MapDriver { driverId: string; name: string; position: Pt; assignmentVersion: number; eligible: boolean; coverageRecordId: string }
export interface MapRoute { routeId: string; points: Pt[]; zoneIds: string[]; status: 'proposed' | 'active' | 'replaced' | 'denied' }
export interface MapZone { zoneId: string; label: string; polygon: Pt[]; permitted: boolean }
export interface MapPlace { placeId: string; label: string; position: Pt; radiusMetres: number }
export interface MapState {
  drivers: MapDriver[]
  merchant: MapPlace
  destination: MapPlace
  zones: MapZone[]
  fuelStops: MapPlace[]
  routes: MapRoute[]
}
export interface MapClause { clauseId: string; prose: string; passed: boolean; observed?: string; expected?: string }
export interface MapPlan { planId: string; replaces: string | null; actions: string[]; reason: string }
/** Position-only event. It carries no amount, currency or payment fields: the map has no payment authority. */
export interface PositionEvent {
  type: 'position'
  driverId: string
  x: number
  y: number
  assignmentVersion: number
  simTime: number
  source: 'simulation'
}
export type DataMode = 'fixture' | 'mock' | 'stripe_test'
