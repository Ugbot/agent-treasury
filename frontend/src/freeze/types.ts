export interface FreezeState {
  active: boolean
  version: number
  since: string | null
  by: string | null
  reason: string | null
}
export interface FreezeClient {
  kind: 'http' | 'fixture'
  get(): Promise<FreezeState>
  set(active: boolean, reason: string): Promise<FreezeState>
}
