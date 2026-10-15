import type { FeedEvent } from '../types'
import { ModeBadge, SourceBadge } from './ModeBadge'

export function EventFeed({ events }: { events: FeedEvent[] }) {
  return (
    <section aria-labelledby="feed-h" className="panel">
      <h2 id="feed-h">Event feed</h2>
      {events.length === 0 ? <p>No events yet.</p> : (
        <ol className="feed" aria-live="polite">
          {events.map((e) => (
            <li key={e.eventId}>
              <span className="t">t+{e.simTime}s</span> <strong>{e.type}</strong> <SourceBadge source={e.source} /> <ModeBadge mode={e.mode} />
              <div>{e.description}</div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
