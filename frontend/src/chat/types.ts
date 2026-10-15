export interface Citation {
  policyId: string
  version: number
  clauseId: string
  evidenceIds: string[]
  decisionId: string
}

export interface ChatReply {
  messageId: string
  text: string
  provider: string
  model: string
  citations: Citation[]
  draftId?: string
  /** Editable draft body when the server attaches one. */
  draftText?: string
}

export type ChatResult = { ok: true; reply: ChatReply } | { ok: false; unavailable: true; message: string }

export interface ChatRequest {
  commandId: string
  text: string
  historyIds: string[]
}

export interface ChatTransport {
  /** onDelta receives partial text for streaming-capable transports. */
  send(missionId: string, req: ChatRequest, onDelta?: (chunk: string) => void): Promise<ChatResult>
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  streaming?: boolean
  provider?: string
  model?: string
  citations?: Citation[]
  draftId?: string
  draftText?: string
}
