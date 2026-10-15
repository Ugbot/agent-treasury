import { useRef, useState } from 'react'
import type { Decision } from '../types'
import { unavailableTransport } from './transport'
import type { ChatMessage, ChatTransport, Citation } from './types'

interface Props {
  missionId: string
  decision: Decision | null
  transport?: ChatTransport
  /** Local delivery only; chat can never mutate money state. */
  onSendDraft?: (draftId: string | undefined, text: string) => void
}

let seq = 0
const uid = (p: string) => `${p}-${Date.now().toString(36)}-${++seq}`

function Explanation({ c, decision }: { c: Citation; decision: Decision | null }) {
  const linked = decision && decision.decisionId === c.decisionId
  const clause = linked ? decision.clauses.find((x) => x.clauseId === c.clauseId) : undefined
  return (
    <li data-testid="citation">
      <strong>{c.clauseId}</strong> ({c.policyId} v{c.version})
      {clause ? (
        <span>
          {' '}
          {clause.passed ? 'passed' : 'failed'}: {clause.prose} (observed {clause.observed}, expected {clause.expected})
        </span>
      ) : (
        <span> decision {c.decisionId} is not the current decision</span>
      )}
      {c.evidenceIds.length > 0 && <small> evidence: {c.evidenceIds.join(', ')}</small>}
    </li>
  )
}

function Draft({ m, onSend }: { m: ChatMessage; onSend: (text: string) => void }) {
  const [text, setText] = useState(m.draftText ?? '')
  const [sent, setSent] = useState(false)
  return (
    <div className="chat-draft">
      <label>
        Draft message
        <textarea value={text} disabled={sent} onChange={(e) => setText(e.target.value)} rows={4} />
      </label>
      {sent ? (
        <p role="status">Draft sent locally.</p>
      ) : (
        <button type="button" disabled={!text.trim()} onClick={() => { onSend(text); setSent(true) }}>
          Send draft
        </button>
      )}
    </div>
  )
}

export function ChatSidebar({ missionId, decision, transport = unavailableTransport, onSendDraft }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [unavailable, setUnavailable] = useState<string | null>(null)
  const history = useRef<string[]>([])

  async function submit() {
    const text = input.trim()
    if (!text || busy || unavailable) return
    const userMsg: ChatMessage = { id: uid('u'), role: 'user', text }
    const pendingId = uid('a')
    setMessages((m) => [...m, userMsg, { id: pendingId, role: 'assistant', text: '', streaming: true }])
    setInput('')
    setBusy(true)
    const ids = [...history.current]
    history.current.push(userMsg.id)
    const res = await transport.send(
      missionId,
      { commandId: uid('cmd'), text, historyIds: ids },
      (chunk) => setMessages((m) => m.map((x) => (x.id === pendingId ? { ...x, text: x.text + chunk } : x))),
    )
    if (res.ok) {
      const r = res.reply
      history.current.push(r.messageId)
      setMessages((m) =>
        m.map((x) =>
          x.id === pendingId
            ? { ...x, id: r.messageId, text: r.text, streaming: false, provider: r.provider, model: r.model, citations: r.citations, draftId: r.draftId, draftText: r.draftText }
            : x,
        ),
      )
    } else {
      setUnavailable(res.message)
      setMessages((m) => m.filter((x) => x.id !== pendingId))
    }
    setBusy(false)
  }

  return (
    <aside className="chat" aria-label="AI assistant">
      <h2>Assistant</h2>
      {unavailable && (
        <div role="alert" className="error" data-testid="chat-unavailable">
          {unavailable}
        </div>
      )}
      <div role="log" aria-live="polite" aria-busy={busy} className="chat-log">
        {messages.map((m) => (
          <div key={m.id} className={`chat-msg ${m.role}`} data-testid={`msg-${m.role}`}>
            <p>{m.text || (m.streaming ? '...' : '')}</p>
            {m.role === 'assistant' && !m.streaming && m.provider && (
              <small>
                {m.provider} / {m.model}
              </small>
            )}
            {m.citations && m.citations.length > 0 && (
              <ul>
                {m.citations.map((c, i) => (
                  <Explanation key={i} c={c} decision={decision} />
                ))}
              </ul>
            )}
            {m.draftText !== undefined && <Draft m={m} onSend={(t) => onSendDraft?.(m.draftId, t)} />}
          </div>
        ))}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); void submit() }}>
        <label>
          Ask about this decision
          <textarea value={input} onChange={(e) => setInput(e.target.value)} disabled={!!unavailable} rows={2} />
        </label>
        <button type="submit" disabled={busy || !!unavailable || !input.trim()}>
          Ask
        </button>
      </form>
    </aside>
  )
}
