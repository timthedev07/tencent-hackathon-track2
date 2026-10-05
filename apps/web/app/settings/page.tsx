'use client';

import { useState, useEffect, useCallback, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { ExternalLink, CheckCircle2, XCircle, AlertCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  ApiClientError,
  getAuthToken,
  getConnections,
  startOAuth,
  type ConnectionStatus,
} from '@/lib/api/client';

const PROVIDERS = [
  { label: 'Atlassian', key: 'atlassian' as const, source: 'confluence' },
  { label: 'Slack', key: 'slack' as const, source: 'slack' },
  { label: 'Google Drive', key: 'google' as const, source: 'google_drive' },
];

function SettingsContent() {
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [connections, setConnections] = useState<Record<string, ConnectionStatus>>({});
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState<string | null>(null);

  const refreshConnections = useCallback(async () => {
    try {
      const data = await getConnections();
      const map: Record<string, ConnectionStatus> = {};
      for (const c of data.connections) {
        map[c.source] = c;
      }
      setConnections(map);
    } catch (e) {
      if (e instanceof ApiClientError && e.status === 401) {
        setError('Please sign in first.');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshConnections();
  }, [refreshConnections]);

  // Handle OAuth callback redirect params
  useEffect(() => {
    const status = searchParams.get('status');
    const source = searchParams.get('source');
    const errorMsg = searchParams.get('error');

    if (status === 'connected' && source) {
      const label = PROVIDERS.find((p) => p.source === source)?.label ?? source;
      setSuccess(`${label} connected successfully!`);
      refreshConnections();
    } else if (status === 'error' && source) {
      const label = PROVIDERS.find((p) => p.source === source)?.label ?? source;
      setError(`${label} connection failed${errorMsg ? `: ${decodeURIComponent(errorMsg)}` : ''}`);
    }
  }, [searchParams, refreshConnections]);

  const hasToken = !!getAuthToken();

  async function handleConnect(provider: 'atlassian' | 'slack' | 'google', label: string) {
    setError(null);
    setSuccess(null);
    setConnecting(label);
    try {
      const { authoriseUrl } = await startOAuth(provider);
      window.location.href = authoriseUrl;
    } catch (e) {
      setError(e instanceof ApiClientError ? e.message : `Failed to start ${label} connection.`);
      setConnecting(null);
    }
  }

  if (loading) {
    return (
      <main className="mx-auto flex min-h-screen max-w-2xl flex-col p-8">
        <h1 className="mb-6 text-2xl font-bold tracking-tight">Connection settings</h1>
        <p className="text-muted-foreground">Loading...</p>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col p-8">
      <h1 className="mb-6 text-2xl font-bold tracking-tight">Connection settings</h1>

      {!hasToken && (
        <div className="mb-4 rounded-lg border border-yellow-300 bg-yellow-50 p-4 text-sm text-yellow-800">
          <AlertCircle className="mb-1 inline h-4 w-4" /> You need to{' '}
          <a href="/login" className="underline">sign in</a> before connecting sources.
        </div>
      )}

      {success && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-green-300 bg-green-50 p-4 text-sm text-green-800">
          <CheckCircle2 className="h-4 w-4" /> {success}
        </div>
      )}
      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800">
          <XCircle className="h-4 w-4" /> {error}
        </div>
      )}

      <div className="space-y-4">
        {PROVIDERS.map((p) => {
          const conn = connections[p.source];
          const isConnected = conn?.connected ?? false;
          return (
            <Card key={p.label}>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base">{p.label}</CardTitle>
                  <Badge variant={isConnected ? 'default' : 'secondary'}>
                    {isConnected ? 'Connected' : 'Not connected'}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent>
                {isConnected && conn?.display_name && (
                  <p className="mb-2 text-sm text-muted-foreground">{conn.display_name}</p>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleConnect(p.key, p.label)}
                  disabled={!hasToken || connecting === p.label}
                >
                  <ExternalLink className="mr-2 h-4 w-4" />
                  {connecting === p.label ? 'Connecting...' : isConnected ? `Reconnect ${p.label}` : `Connect ${p.label}`}
                </Button>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </main>
  );
}

export default function SettingsPage() {
  return (
    <Suspense fallback={
      <main className="mx-auto flex min-h-screen max-w-2xl flex-col p-8">
        <h1 className="mb-6 text-2xl font-bold tracking-tight">Connection settings</h1>
        <p className="text-muted-foreground">Loading...</p>
      </main>
    }>
      <SettingsContent />
    </Suspense>
  );
}
