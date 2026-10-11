export type SseEventType =
  | 'query.queued'
  | 'query.authenticated'
  | 'query.retrieving'
  | 'query.reauthorising'
  | 'query.reranking'
  | 'query.generating'
  | 'query.verifying'
  | 'query.auditing'
  | 'query.completed'
  | 'query.partial'
  | 'query.denied'
  | 'query.failed'

export interface SseEvent {
  event: SseEventType
  data: { queryId: string; message?: string; errorCode?: string }
}
export interface SseError {
  message: string
  code?: string
  status?: number
}
export interface SseHandlers {
  onEvent: (event: SseEvent) => void
  onError?: (error: SseError) => void
  onClose?: () => void
}

class SseConnectionError extends Error implements SseError {
  code?: string
  status?: number

  constructor(message: string, status?: number, code?: string) {
    super(message)
    this.name = 'SseConnectionError'
    this.status = status
    this.code = code
  }
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const API_PREFIX = '/v1'

export class SseStream {
  private abortController: AbortController | null = null
  private reconnectAttempts = 0
  private readonly maxReconnectAttempts = 5
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private lastEventId: string | null = null

  constructor(
    private readonly queryId: string,
    private readonly handlers: SseHandlers,
  ) {}

  async connect(): Promise<void> {
    const token = typeof window !== 'undefined' ? window.localStorage.getItem('auth_token') : null
    if (!token) {
      this.handlers.onError?.({
        message: 'Your session has expired. Please sign in again.',
        code: 'AUTH_INVALID_TOKEN',
      })
      return
    }
    this.abortController = new AbortController()
    try {
      const headers: Record<string, string> = {
        Authorization: `Bearer ${token}`,
        Accept: 'text/event-stream',
      }
      if (this.lastEventId) headers['Last-Event-ID'] = this.lastEventId
      const response = await fetch(`${API_URL}${API_PREFIX}/queries/${this.queryId}/events`, {
        headers,
        signal: this.abortController.signal,
        cache: 'no-store',
      })
      if (!response.ok || !response.body) {
        const body = (await response.json().catch(() => null)) as {
          error?: { code?: string; message?: string }
        } | null
        throw new SseConnectionError(
          body?.error?.message ?? `The search stream could not be opened (${response.status}).`,
          response.status,
          body?.error?.code,
        )
      }
      const completed = await this.readEvents(response.body)
      if (!completed) throw new Error('The search stream ended before the query completed.')
      this.reconnectAttempts = 0
      this.handlers.onClose?.()
    } catch (error) {
      if (this.abortController?.signal.aborted) return
      const invalidEvent =
        error instanceof SseConnectionError && error.code === 'INVALID_STREAM_EVENT'
      if (invalidEvent || this.reconnectAttempts >= this.maxReconnectAttempts) {
        this.close()
        this.handlers.onError?.({
          message: error instanceof Error ? error.message : 'The search stream ended unexpectedly.',
          code: error instanceof SseConnectionError ? error.code : undefined,
          status: error instanceof SseConnectionError ? error.status : undefined,
        })
        return
      }
      this.reconnectAttempts += 1
      const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 16000)
      this.reconnectTimer = setTimeout(() => void this.connect(), delay)
    }
  }

  private async readEvents(body: ReadableStream<Uint8Array>): Promise<boolean> {
    const reader = body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let eventName = ''
    let data = ''
    let eventId = ''
    const terminalEvents = new Set([
      'query.completed',
      'query.partial',
      'query.denied',
      'query.failed',
    ])
    const dispatchEvent = (): boolean => {
      if (!eventName) return false
      let parsed: { queryId?: string; message?: string; errorCode?: string }
      try {
        parsed = JSON.parse(data.trim() || '{}')
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
          throw new Error('Invalid event payload')
      } catch {
        throw new SseConnectionError(
          'The search stream returned invalid progress data.',
          undefined,
          'INVALID_STREAM_EVENT',
        )
      }
      if (eventId) this.lastEventId = eventId
      this.handlers.onEvent({
        event: eventName as SseEventType,
        data: {
          queryId: parsed.queryId ?? this.queryId,
          message: parsed.message,
          errorCode: parsed.errorCode,
        },
      })
      const isTerminal = terminalEvents.has(eventName)
      eventName = ''
      data = ''
      eventId = ''
      if (isTerminal) this.close()
      return isTerminal
    }

    const processLine = (line: string): boolean => {
      if (line.startsWith('event:')) eventName = line.slice(6).trim()
      else if (line.startsWith('data:')) data += `${line.slice(5).trim()}\n`
      else if (line.startsWith('id:')) eventId = line.slice(3).trim()
      else if (line === '') return dispatchEvent()
      return false
    }

    while (true) {
      const { value, done } = await reader.read()
      if (done) {
        // Some proxies close immediately after writing the final event and do
        // not provide a second blank line. Flush the decoder and pending event
        // before deciding that the stream ended prematurely.
        buffer += decoder.decode()
        for (const line of buffer.split(/\r?\n/)) {
          if (processLine(line)) return true
        }
        return dispatchEvent()
      }
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        if (processLine(line)) return true
      }
    }
  }

  close(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.abortController?.abort()
    this.abortController = null
  }
}
