const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const API_PREFIX = '/v1';

function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem('auth_token');
}

interface ApiErrorDetail {
  error?: { code?: string; message?: string };
}

interface ApiError extends ApiErrorDetail {
  detail?: ApiErrorDetail | string;
}

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const response = await fetch(`${API_URL}${API_PREFIX}${path}`, { ...options, headers });
  if (!response.ok) {
    const errorBody = (await response.json().catch(() => null)) as ApiError | null;
    const detailError = typeof errorBody?.detail === 'object' ? errorBody.detail.error : undefined;
    const error = errorBody?.error ?? detailError;
    const code = error?.code ?? 'UNKNOWN_ERROR';
    const message = error?.message ?? (typeof errorBody?.detail === 'string' ? errorBody.detail : 'An error occurred.');
    throw new ApiClientError(code, message, response.status);
  }
  return response.json() as Promise<T>;
}

export class ApiClientError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = 'ApiClientError';
    this.code = code;
    this.status = status;
  }
}

export interface CreateQueryResponse { queryId: string; status: string }
export interface Citation { evidenceId: string; source: string; label: string | null; url: string | null }
export interface QueryResult {
  queryId: string; status: string; answer: string | null;
  citations: Citation[]; partialSourceWarnings: { source: string; reason: string }[];
  errorCode: string | null; errorMessage: string | null;
}
export interface EvidenceResponse {
  evidenceId: string; source?: string; title?: string | null;
  quotedSpan?: string | null; canonicalUrl?: string | null; status?: string;
}
export interface AuditResponse {
  queryId: string; timestamp: string | null; userIdentifier: string | null;
  queryStatus: string | null; modelVersion: string | null; promptVersion: string | null;
  accessDecisions: { source: string; decision: string; reasonCode: string | null; accessCheckedAt: string | null }[];
  evidenceManifest: { source: string; rank: number; sourceRevision: string }[];
  citations: { evidenceId: string; source: string; rank: number; sourceRevision: string }[];
  auditChainVerified: boolean; partialSources: unknown[]; errors: unknown[];
}
export interface OAuthStartResponse { authoriseUrl: string }

export async function createQuery(query: string): Promise<CreateQueryResponse> {
  return apiFetch<CreateQueryResponse>('/queries', { method: 'POST', body: JSON.stringify({ query }) });
}
export async function getQueryResult(queryId: string): Promise<QueryResult> {
  return apiFetch<QueryResult>(`/queries/${queryId}`);
}
export async function getEvidence(evidenceId: string): Promise<EvidenceResponse> {
  return apiFetch<EvidenceResponse>(`/evidence/${evidenceId}`);
}
export async function getAuditCapsule(queryId: string): Promise<AuditResponse> {
  return apiFetch<AuditResponse>(`/audit/${queryId}`);
}
export async function startOAuth(provider: 'atlassian' | 'slack' | 'google'): Promise<OAuthStartResponse> {
  return apiFetch<OAuthStartResponse>(`/oauth/${provider}/start`);
}

export interface ConnectionStatus {
  source: string;
  connected: boolean;
  display_name: string | null;
  connectionId: string | null;
}
export interface ConnectionsResponse {
  connections: ConnectionStatus[];
}

export async function getConnections(): Promise<ConnectionsResponse> {
  return apiFetch<ConnectionsResponse>('/oauth/connections');
}

export interface SyncConnectionResponse { connectionId: string; runId: string; status: 'queued' }

export async function syncConnection(connectionId: string): Promise<SyncConnectionResponse> {
  return apiFetch<SyncConnectionResponse>(`/oauth/connections/${connectionId}/sync`, {
    method: 'POST',
  });
}

export async function restartConnection(connectionId: string): Promise<SyncConnectionResponse> {
  return apiFetch<SyncConnectionResponse>(`/oauth/connections/${connectionId}/restart`, {
    method: 'POST',
  });
}

export interface BackfillRunStatus {
  id: string; source: string; status: 'queued' | 'scanning' | 'processing' | 'completed' | 'completed_with_errors' | 'failed';
  discoveredCount: number; enqueuedCount: number; processedCount: number; failedCount: number;
  errorMessage: string | null; startedAt: string | null; completedAt: string | null;
}
export interface ManualBackfillResponse { queuedSources: string[]; message: string; runs: BackfillRunStatus[] }

export async function triggerManualBackfill(provider: 'atlassian' | 'slack' | 'google_drive'): Promise<ManualBackfillResponse> {
  return apiFetch<ManualBackfillResponse>(`/oauth/connections/${provider}/backfill`, { method: 'POST' });
}

export async function getBackfillStatus(): Promise<{ runs: BackfillRunStatus[] }> {
  return apiFetch<{ runs: BackfillRunStatus[] }>('/oauth/connections/backfill-status');
}

export interface BackfillPreviewItem {
  source: string; title: string; url: string | null; updatedAt: string | null; snippet: string | null;
}
export interface BackfillPreviewResponse {
  atlassian: { pages: BackfillPreviewItem[]; tickets: BackfillPreviewItem[] };
  slack: BackfillPreviewItem[];
  googleDrive: BackfillPreviewItem[];
}

export async function getBackfillPreviews(): Promise<BackfillPreviewResponse> {
  return apiFetch<BackfillPreviewResponse>('/oauth/connections/previews');
}

export async function devLogin(subject: string): Promise<{ token: string; subject: string }> {
  const response = await fetch(`${API_URL}${API_PREFIX}/auth/dev-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ subject }),
  });
  if (!response.ok) throw new Error('Dev login failed');
  return response.json();
}

export function setAuthToken(token: string): void {
  if (typeof window !== 'undefined') {
    window.localStorage.setItem('auth_token', token);
  }
}

export function getAuthToken(): string | null {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem('auth_token');
}

export function clearAuthToken(): void {
  if (typeof window !== 'undefined') {
    window.localStorage.removeItem('auth_token');
  }
}
