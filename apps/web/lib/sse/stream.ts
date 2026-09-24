export type SseEventType =
  | 'query.authenticated' | 'query.retrieving' | 'query.reauthorising'
  | 'query.reranking' | 'query.generating' | 'query.verifying'
  | 'query.auditing' | 'query.completed' | 'query.partial'
  | 'query.denied' | 'query.failed';

export interface SseEvent { event: SseEventType; data: { queryId: string; message?: string } }
export interface SseHandlers { onEvent: (event: SseEvent) => void; onError?: (error: Event) => void; onClose?: () => void }

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const API_PREFIX = '/v1';

export class SseStream {
  private eventSource: EventSource | null = null;
  private reconnectAttempts = 0;
  private readonly maxReconnectAttempts = 5;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly queryId: string, private readonly handlers: SseHandlers) {}

  connect(): void {
    const token = typeof window !== 'undefined' ? window.localStorage.getItem('auth_token') : null;
    const url = `${API_URL}${API_PREFIX}/queries/${this.queryId}/events${token ? `?token=${encodeURIComponent(token)}` : ''}`;
    this.eventSource = new EventSource(url);

    const eventTypes: SseEventType[] = [
      'query.authenticated', 'query.retrieving', 'query.reauthorising',
      'query.reranking', 'query.generating', 'query.verifying',
      'query.auditing', 'query.completed', 'query.partial', 'query.denied', 'query.failed',
    ];

    for (const type of eventTypes) {
      this.eventSource.addEventListener(type, (e: MessageEvent) => {
        this.reconnectAttempts = 0;
        try {
          const data = JSON.parse(e.data) as { queryId: string; message?: string };
          this.handlers.onEvent({ event: type, data });
        } catch {
          this.handlers.onEvent({ event: type, data: { queryId: this.queryId } });
        }
        if (['query.completed', 'query.partial', 'query.denied', 'query.failed'].includes(type)) this.close();
      });
    }

    this.eventSource.onerror = (e: Event) => {
      if (this.eventSource?.readyState === EventSource.CLOSED) { this.handlers.onClose?.(); return; }
      this.handleReconnect();
      this.handlers.onError?.(e);
    };
  }

  private handleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this.close();
      this.handlers.onError?.(new Event('max_reconnect_exceeded'));
      return;
    }
    this.reconnectAttempts += 1;
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 16000);
    this.eventSource?.close();
    this.eventSource = null;
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  close(): void {
    if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
    this.eventSource?.close();
    this.eventSource = null;
  }
}
