export type SseEventType =
  | 'query.queued' | 'query.authenticated' | 'query.retrieving' | 'query.reauthorising'
  | 'query.reranking' | 'query.generating' | 'query.verifying'
  | 'query.auditing' | 'query.completed' | 'query.partial'
  | 'query.denied' | 'query.failed';

export interface SseEvent { event: SseEventType; data: { queryId: string; message?: string } }
export interface SseHandlers { onEvent: (event: SseEvent) => void; onError?: (error: Event) => void; onClose?: () => void }

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const API_PREFIX = '/v1';

export class SseStream {
  private abortController: AbortController | null = null;
  private reconnectAttempts = 0;
  private readonly maxReconnectAttempts = 5;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private lastEventId: string | null = null;

  constructor(private readonly queryId: string, private readonly handlers: SseHandlers) {}

  async connect(): Promise<void> {
    const token = typeof window !== 'undefined' ? window.localStorage.getItem('auth_token') : null;
    if (!token) {
      this.handlers.onError?.(new Event('missing_auth_token'));
      return;
    }
    this.abortController = new AbortController();
    try {
      const headers: Record<string, string> = {
        Authorization: `Bearer ${token}`,
        Accept: 'text/event-stream',
      };
      if (this.lastEventId) headers['Last-Event-ID'] = this.lastEventId;
      const response = await fetch(`${API_URL}${API_PREFIX}/queries/${this.queryId}/events`, {
        headers,
        signal: this.abortController.signal,
        cache: 'no-store',
      });
      if (!response.ok || !response.body) throw new Error(`SSE request failed: ${response.status}`);
      this.reconnectAttempts = 0;
      await this.readEvents(response.body);
      this.handlers.onClose?.();
    } catch (error) {
      if (this.abortController?.signal.aborted) return;
      const event = new Event(error instanceof Error ? error.message : 'sse_error');
      if (this.reconnectAttempts >= this.maxReconnectAttempts) {
        this.close();
        this.handlers.onError?.(new Event('max_reconnect_exceeded'));
        return;
      }
      this.reconnectAttempts += 1;
      const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 16000);
      this.handlers.onError?.(event);
      this.reconnectTimer = setTimeout(() => void this.connect(), delay);
    }
  }

  private async readEvents(body: ReadableStream<Uint8Array>): Promise<void> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let eventName = '';
    let data = '';
    let eventId = '';
    const terminalEvents = new Set(['query.completed', 'query.partial', 'query.denied', 'query.failed']);
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim();
        else if (line.startsWith('data:')) data += `${line.slice(5).trim()}\n`;
        else if (line.startsWith('id:')) eventId = line.slice(3).trim();
        else if (line === '') {
          if (eventName) {
            const parsed = JSON.parse(data.trim() || '{}') as { queryId: string; message?: string };
            if (eventId) this.lastEventId = eventId;
            this.handlers.onEvent({ event: eventName as SseEventType, data: { queryId: parsed.queryId ?? this.queryId, message: parsed.message } });
            if (terminalEvents.has(eventName)) {
              this.close();
              return;
            }
          }
          eventName = '';
          data = '';
          eventId = '';
        }
      }
    }
  }

  close(): void {
    if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
    this.abortController?.abort();
    this.abortController = null;
  }
}
