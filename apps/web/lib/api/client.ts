const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const API_PREFIX = '/v1';

function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem('auth_token');
}

interface ApiError {
  error: { code: string; message: string };
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
    const code = errorBody?.error?.code ?? 'UNKNOWN_ERROR';
    const message = errorBody?.error?.message ?? 'An error occurred.';
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
}
export interface ConnectionsResponse { connections: ConnectionStatus[] }

export async function getConnections(): Promise<ConnectionsResponse> {
  return apiFetch<ConnectionsResponse>('/oauth/connections');
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
