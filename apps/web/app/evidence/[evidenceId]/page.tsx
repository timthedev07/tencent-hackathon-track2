'use client';

import { use, useEffect, useState } from 'react';
import { Lock, ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { ApiClientError, getEvidence, type EvidenceResponse } from '@/lib/api/client';

export default function EvidencePage({ params }: { params: Promise<{ evidenceId: string }> }) {
  const { evidenceId } = use(params);
  const [evidence, setEvidence] = useState<EvidenceResponse | null>(null);
  const [accessRevoked, setAccessRevoked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      setIsLoading(true);
      try {
        const result = await getEvidence(evidenceId);
        if (result.status === 'access_revoked') setAccessRevoked(true);
        else setEvidence(result);
      } catch (e) {
        if (e instanceof ApiClientError && e.status === 403) setAccessRevoked(true);
        else setError(e instanceof ApiClientError ? e.message : 'Failed to load evidence.');
      } finally { setIsLoading(false); }
    }
    load();
  }, [evidenceId]);

  if (isLoading) return <main className="mx-auto flex min-h-screen max-w-2xl items-center justify-center p-8"><p className="text-muted-foreground">Checking access…</p></main>;

  if (accessRevoked) return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center p-8">
      <Card><CardContent className="flex flex-col items-center space-y-4 p-8 text-center">
        <Lock className="h-12 w-12 text-muted-foreground" />
        <h1 className="text-lg font-semibold">Access revoked</h1>
        <p className="text-sm text-muted-foreground">You no longer have access to this content. Access was revoked since the answer was generated.</p>
        <Button variant="outline" asChild><Link href="/query"><ArrowLeft className="mr-2 h-4 w-4" /> Back to query</Link></Button>
      </CardContent></Card>
    </main>
  );

  if (error || !evidence) return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center p-8">
      <Alert variant="destructive"><AlertTitle>Error</AlertTitle><AlertDescription>{error ?? 'Evidence not found.'}</AlertDescription></Alert>
    </main>
  );

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col p-8">
      <div className="mb-4"><Button variant="ghost" size="sm" asChild><Link href="/query"><ArrowLeft className="mr-2 h-4 w-4" /> Back</Link></Button></div>
      <Card><CardContent className="p-6">
        <div className="mb-3"><Badge variant="secondary">{evidence.source}</Badge></div>
        <h1 className="mb-4 text-lg font-semibold">{evidence.title ?? 'Untitled evidence'}</h1>
        <div className="border-l-2 border-primary pl-4"><p className="whitespace-pre-wrap text-foreground">{evidence.quotedSpan ?? 'No content available.'}</p></div>
        {evidence.canonicalUrl && <a href={evidence.canonicalUrl} target="_blank" rel="noopener noreferrer" className="mt-4 inline-block text-sm text-primary hover:underline">View original source</a>}
      </CardContent></Card>
    </main>
  );
}
