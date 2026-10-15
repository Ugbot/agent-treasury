import { useState } from 'react'
import { errMsg } from '../lib/wire'
import type { FetchImpl } from '../lib/wire'
import { fetchAuditPack } from './client'

export function PackButton({ mission, fetchImpl }: { mission: string; fetchImpl?: FetchImpl }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const go = async () => {
    setBusy(true)
    setErr(null)
    try {
      const blob = await fetchAuditPack(mission, fetchImpl)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `audit-pack-${mission}.zip`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setErr(errMsg(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <button type="button" onClick={go} disabled={busy}>{busy ? 'Preparing...' : 'Download audit pack'}</button>
      {err && <span role="alert" className="error inline">Could not download: {err}</span>}
    </>
  )
}
