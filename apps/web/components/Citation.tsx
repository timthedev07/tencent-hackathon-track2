'use client';

import { useState } from 'react';
import { ExternalLink, Lock } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { ApiClientError, getEvidence, type Citation as CitationType } from '@/lib/api/client';

const SOURCE_LABELS: Record<string, string> = {
  confluence: 'Confluence',
  jira: 'Jira',
  slack: 'Slack',
  google_drive: 'Google Drive',
};

export function Citation({ citation }: { citation: CitationType }) {
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [evidenceText, setEvidenceText] = useState<string | null>(null);
  const [accessRevoked, setAccessRevoked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleOpen() {
    if (isOpen) { setIsOpen(false); return; }
    setIsLoading(true);
    setError(null);
    setAccessRevoked(false);
    setEvidenceText(null);

    try {
      const result = await getEvidence(citation.evidenceId);
      if (result.status === 'access_revoked') {
        setAccessRevoked(true);
      } else {
        setEvidenceText(result.quotedSpan ?? null);
        setIsOpen(true);
      }
    } catch (e) {
      if (e instanceof ApiClientError) {
        if (e.status === 403) setAccessRevoked(true);
        else setError(e.message);
      } else {
        setError('Unable to load evidence.');
      }
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-start justify-between space-x-2">
          <div className="flex-1">
            <div className="mb-1 flex items-center space-x-2">
              <Badge variant="secondary">{SOURCE_LABELS[citation.source] ?? citation.source}</Badge>
              {citation.url && (
                <a href={citation.url} target="_blank" rel="noopener noreferrer" className="text-xs text-muted-foreground hover:text-primary">
                  <ExternalLink className="inline h-3 w-3" /> Open source
                </a>
              )}
            </div>
            <p className="text-sm font-medium">{citation.label ?? 'Untitled evidence'}</p>
          </div>
          <Button variant="outline" size="sm" onClick={handleOpen} disabled={isLoading}>
            {isLoading ? 'Checking access…' : isOpen ? 'Hide' : 'View'}
          </Button>
        </div>

        {accessRevoked && (
          <div className="mt-3 flex items-center space-x-2 rounded-md bg-muted p-2 text-sm text-muted-foreground">
            <Lock className="h-4 w-4" />
            <span>You no longer have access to this content. Access was revoked since the answer was generated.</span>
          </div>
        )}
        {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
        {isOpen && evidenceText && (
          <div className="mt-3 border-l-2 border-primary pl-3">
            <p className="text-sm text-foreground">{evidenceText}</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
