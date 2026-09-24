'use client';

import { use, useEffect, useState } from 'react';
import { ShieldCheck, ShieldAlert } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiClientError, getAuditCapsule, type AuditResponse } from '@/lib/api/client';
import Link from 'next/link';

export default function AuditPage({ params }: { params: Promise<{ queryId: string }> }) {
  const { queryId } = use(params);
  const [audit, setAudit] = useState<AuditResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      setIsLoading(true);
      try { setAudit(await getAuditCapsule(queryId)); }
      catch (e) { setError(e instanceof ApiClientError ? e.message : 'Failed to load audit capsule.'); }
      finally { setIsLoading(false); }
    }
    load();
  }, [queryId]);

  if (isLoading) return <main className="mx-auto flex min-h-screen max-w-4xl items-center justify-center p-8"><p className="text-muted-foreground">Loading audit record…</p></main>;

  if (error || !audit) return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col justify-center p-8">
      <Alert variant="destructive"><AlertTitle>Access denied or not found</AlertTitle><AlertDescription>{error ?? 'You may not have the compliance role, or this query does not exist.'}</AlertDescription></Alert>
      <div className="mt-4"><Button variant="outline" asChild><Link href="/query">Back to query</Link></Button></div>
    </main>
  );

  return (
    <main className="mx-auto min-h-screen max-w-4xl p-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Audit record</h1>
        {audit.auditChainVerified ? <Badge variant="default" className="gap-1"><ShieldCheck className="h-3 w-3" /> Chain verified</Badge> : <Badge variant="destructive" className="gap-1"><ShieldAlert className="h-3 w-3" /> Chain verification failed</Badge>}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card><CardHeader><CardTitle>Query details</CardTitle></CardHeader><CardContent className="space-y-2 text-sm">
          <Row label="Query ID" value={audit.queryId} />
          <Row label="Status" value={audit.queryStatus ?? '—'} />
          <Row label="Timestamp" value={audit.timestamp ?? '—'} />
          <Row label="User" value={audit.userIdentifier ?? '—'} />
        </CardContent></Card>
        <Card><CardHeader><CardTitle>Model and prompt versions</CardTitle></CardHeader><CardContent className="space-y-2 text-sm">
          <Row label="Model" value={audit.modelVersion ?? '—'} />
          <Row label="Prompt" value={audit.promptVersion ?? '—'} />
        </CardContent></Card>
      </div>

      <Card className="mt-4"><CardHeader><CardTitle>Access decisions</CardTitle></CardHeader><CardContent>
        {audit.accessDecisions.length === 0 ? <p className="text-sm text-muted-foreground">No access decisions recorded.</p> : (
          <table className="w-full text-sm"><thead><tr className="border-b border-border text-left text-muted-foreground"><th className="py-2 font-medium">Source</th><th className="py-2 font-medium">Decision</th><th className="py-2 font-medium">Reason</th><th className="py-2 font-medium">Checked at</th></tr></thead><tbody>
            {audit.accessDecisions.map((d, i) => (
              <tr key={i} className="border-b border-border"><td className="py-2">{d.source}</td><td className="py-2"><Badge variant={d.decision === 'allow' ? 'default' : 'destructive'}>{d.decision}</Badge></td><td className="py-2">{d.reasonCode ?? '—'}</td><td className="py-2">{d.accessCheckedAt ?? '—'}</td></tr>
            ))}
          </tbody></table>
        )}
      </CardContent></Card>

      <Card className="mt-4"><CardHeader><CardTitle>Evidence manifest</CardTitle></CardHeader><CardContent>
        {audit.evidenceManifest.length === 0 ? <p className="text-sm text-muted-foreground">No evidence was authorised for this query.</p> : (
          <table className="w-full text-sm"><thead><tr className="border-b border-border text-left text-muted-foreground"><th className="py-2 font-medium">Source</th><th className="py-2 font-medium">Rank</th><th className="py-2 font-medium">Revision</th></tr></thead><tbody>
            {audit.evidenceManifest.map((e, i) => (
              <tr key={i} className="border-b border-border"><td className="py-2">{e.source}</td><td className="py-2">{e.rank}</td><td className="py-2">{e.sourceRevision}</td></tr>
            ))}
          </tbody></table>
        )}
      </CardContent></Card>
    </main>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return <div className="flex justify-between"><span className="text-muted-foreground">{label}</span><span className="font-mono text-xs">{value}</span></div>;
}
