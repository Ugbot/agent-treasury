import type { DataMode, EventSource } from '../types'

const LABEL: Record<DataMode, string> = { fixture: 'FIXTURE', mock: 'MOCK', stripe_test: 'STRIPE TEST' }

export function ModeBadge({ mode }: { mode: DataMode }) {
  return <span className={`badge mode-${mode}`} title={`Data mode: ${LABEL[mode]}`}>{LABEL[mode]}</span>
}

export function SourceBadge({ source }: { source: EventSource }) {
  return <span className={`badge source-${source}`}>{source === 'provider' ? 'PROVIDER' : 'SIMULATION'}</span>
}

export const money = (minor: number, cur = 'usd') =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: cur.toUpperCase() }).format(minor / 100)
