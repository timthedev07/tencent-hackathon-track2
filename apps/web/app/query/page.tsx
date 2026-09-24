'use client';

import { useCallback, useState } from 'react';
import { Send, AlertTriangle } from 'lucide-react';
import { Citation } from '@/components/Citation';
import { QueryProgress } from '@/components/QueryProgress';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { SseStream, type SseEventType } from '@/lib/sse/stream';
import { ApiClientError, createQuery, getQueryResult, type Citation as CitationType, type QueryResult } from '@/lib/api/client';

type ViewState = 'idle' | 'streaming' | 'complete' | 'failed';

export default function QueryPage() {
  const [query, setQuery] = useState('');
  const [viewState, setViewState] = useState<ViewState>('idle');
  const [currentStage, setCurrentStage] = useState<SseEventType | null>(null);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadResult = useCallback(async (queryId: string) => {
    try {
      const res = await getQueryResult(queryId);
      setResult(res);
      setViewState('complete');
    } catch (e) {
      setViewState('failed');
      setError(e instanceof ApiClientError ? e.message : 'Failed to load result.');
    }
  }, []);

  const handleSubmit = useCallback(async () => {
    if (!query.trim()) return;
    setViewState('streaming');
    setCurrentStage(null);
    setResult(null);
    setError(null);

    try {
      const { queryId } = await createQuery(query.trim());
      const sse = new SseStream(queryId, {
        onEvent: (event) => {
          setCurrentStage(event.event);
          if (['query.completed', 'query.partial', 'query.denied'].includes(event.event)) {
            loadResult(queryId);
          } else if (event.event === 'query.failed') {
            setViewState('failed');
            setError('The query could not be completed. Please try again.');
          }
        },
        onError: () => { setViewState('failed'); setError('Connection lost. Please try again.'); },
      });
      sse.connect();
    } catch (e) {
      setViewState('failed');
      setError(e instanceof ApiClientError ? e.message : 'Failed to submit query.');
    }
  }, [query, loadResult]);

  const isStreaming = viewState === 'streaming';
  const hasPartialWarnings = (result?.partialSourceWarnings.length ?? 0) > 0;

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col p-8">
      <h1 className="mb-6 text-2xl font-bold tracking-tight">Ask a question</h1>

      <div className="mb-6 flex space-x-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask anything about your connected sources…"
          disabled={isStreaming}
          onKeyDown={(e) => { if (e.key === 'Enter' && !isStreaming) handleSubmit(); }}
        />
        <Button onClick={handleSubmit} disabled={isStreaming || !query.trim()}>
          <Send className="mr-2 h-4 w-4" /> Submit
        </Button>
      </div>

      {isStreaming && (
        <Card className="mb-6"><CardContent className="p-4">
          <QueryProgress currentStage={currentStage} isComplete={false} hasFailed={false} />
        </CardContent></Card>
      )}

      {viewState === 'failed' && (
        <Alert variant="destructive" className="mb-6">
          <AlertTitle>Something went wrong</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {viewState === 'complete' && result && (
        <div className="space-y-6">
          {hasPartialWarnings && (
            <Alert variant="warning">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Partial sources</AlertTitle>
              <AlertDescription>Some sources could not be fully authorised within the time limit. The answer may be incomplete.</AlertDescription>
            </Alert>
          )}
          {result.answer ? (
            <Card><CardContent className="p-6">
              <h2 className="mb-3 text-lg font-semibold">Answer</h2>
              <p className="whitespace-pre-wrap text-foreground">{result.answer}</p>
            </CardContent></Card>
          ) : (
            <Alert>
              <AlertTitle>No answer available</AlertTitle>
              <AlertDescription>{result.status === 'denied' ? 'You do not currently have access to any relevant sources.' : 'The query did not produce an answer.'}</AlertDescription>
            </Alert>
          )}
          {result.citations.length > 0 && (
            <div>
              <h2 className="mb-3 text-lg font-semibold">Sources</h2>
              <div className="space-y-3">
                {result.citations.map((c: CitationType) => <Citation key={c.evidenceId} citation={c} />)}
              </div>
            </div>
          )}
        </div>
      )}
    </main>
  );
}
