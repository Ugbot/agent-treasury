import type { ApiErrorBody } from '../types'

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.code = body.code
    this.status = status
  }
  get isStale() {
    return this.status === 409 && this.code === 'stale_revision'
  }
}
