'use client';

import { useState } from 'react';
import { ExternalLink } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiClientError, startOAuth } from '@/lib/api/client';

const PROVIDERS = [
  { label: 'Atlassian', key: 'atlassian' as const },
  { label: 'Slack', key: 'slack' as const },
  { label: 'Google', key: 'google' as const },
];

export default function SettingsPage() {
  const [error, setError] = useState<string | null>(null);

  async function handleConnect(provider: 'atlassian' | 'slack' | 'google', label: string) {
    setError(null);
    try {
      const { authoriseUrl } = await startOAuth(provider);
      window.location.href = authoriseUrl;
    } catch (e) {
      setError(e instanceof ApiClientError ? e.message : `Failed to start ${label} connection.`);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col p-8">
      <h1 className="mb-6 text-2xl font-bold tracking-tight">Connection settings</h1>
      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}
      <div className="space-y-4">
        {PROVIDERS.map((p) => (
          <Card key={p.label}>
            <CardHeader><div className="flex items-center justify-between"><CardTitle className="text-base">{p.label}</CardTitle><Badge variant="secondary">Not connected</Badge></div></CardHeader>
            <CardContent>
              <Button variant="outline" size="sm" onClick={() => handleConnect(p.key, p.label)}>
                <ExternalLink className="mr-2 h-4 w-4" /> Connect {p.label}
              </Button>
            </CardContent>
          </Card>
        ))}
      </div>
    </main>
  );
}
