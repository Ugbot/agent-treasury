import type { TreasuryClient } from './client'
import { createFixtureClient } from './fixture'
import { createHttpClient } from './http'

export * from './commands'
export { ApiError } from './errors'
export type { TreasuryClient } from './client'
export { createFixtureClient, createHttpClient }

/** Fixture mode is opt-in: VITE_API_MODE=fixture. Otherwise talk to the backend via the Vite proxy. */
export function clientFromEnv(mode: string | undefined = import.meta.env.VITE_API_MODE): TreasuryClient {
  return mode === 'fixture' ? createFixtureClient() : createHttpClient('')
}
