import { useAuditFeed } from '../audit'
import type { AuditClient } from '../audit'
import { ModeChip } from '../components/ModeChip'

const human = (kind: string) => { const t = kind.replace(/[._-]+/g, ' ').trim(); return t.charAt(0).toUpperCase() + t.slice(1) }

export function ActivityFeed({ audit, onOpen, pollMs }: { audit: AuditClient; onOpen: (seq: number) => void; pollMs?: number }) {
  const feed = useAuditFeed(audit, { pollMs })
  const live = feed.transport === 'sse' ? 'Live' : feed.transport === 'polling' ? 'Updating every second' : 'Connecting'
  return (
    <section className="activity" aria-labelledby="act-h">
      <div className="activity-head">
        <h2 id="act-h">Activity</h2>
        <span className="live" data-live={feed.transport !== 'idle'}>{live}</span>
      </div>
      {feed.error && <div role="alert" className="error inline">{feed.error} The feed retries on its own.</div>}
      {feed.entries.length === 0 ? (
        <p className="empty">{feed.loading ? 'Loading activity...' : 'Nothing has happened yet. Run a scenario to see each step land here.'}</p>
      ) : (
        <ol className="act-list" aria-live="polite">
          {[...feed.entries].reverse().slice(0, 40).map((e) => (
            <li key={e.seq}>
              <div className="act-meta">
                <time dateTime={e.ts}>{e.ts.slice(11, 19)}</time>
                <span>{e.actor}</span>
              </div>
              <p className="act-summary">{e.summary}</p>
              <div className="act-foot">
                <span className="act-kind">{human(e.kind)}</span>
                <ModeChip mode={e.mode} />
                <button type="button" className="link" onClick={() => onOpen(e.seq)} aria-label={`Open log entry ${e.seq}`}>Log #{e.seq}</button>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
