'use client';

import { CheckCircle2, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { SseEventType } from '@/lib/sse/stream';

const STAGES: { event: SseEventType; label: string }[] = [
  { event: 'query.authenticated', label: 'Authenticating' },
  { event: 'query.retrieving', label: 'Searching connected sources' },
  { event: 'query.reauthorising', label: 'Checking your current access' },
  { event: 'query.reranking', label: 'Ranking authorised evidence' },
  { event: 'query.generating', label: 'Generating answer' },
  { event: 'query.verifying', label: 'Verifying citations' },
  { event: 'query.auditing', label: 'Saving audit record' },
];

interface QueryProgressProps {
  currentStage: SseEventType | null;
  isComplete: boolean;
  hasFailed: boolean;
}

export function QueryProgress({ currentStage, isComplete, hasFailed }: QueryProgressProps) {
  const currentIndex = currentStage ? STAGES.findIndex((s) => s.event === currentStage) : -1;

  return (
    <div className="flex flex-col space-y-2">
      {STAGES.map((stage, index) => {
        const isDone = isComplete || index < currentIndex;
        const isActive = index === currentIndex && !isComplete && !hasFailed;
        return (
          <div
            key={stage.event}
            className={cn(
              'flex items-center space-x-3 rounded-md px-3 py-2 text-sm',
              isActive && 'bg-muted font-medium',
              !isActive && !isDone && 'text-muted-foreground',
              hasFailed && index >= currentIndex && 'text-muted-foreground',
            )}
          >
            {isDone ? (
              <CheckCircle2 className="h-4 w-4 text-primary" />
            ) : isActive ? (
              <Loader2 className="h-4 w-4 animate-spin text-primary" />
            ) : (
              <div className="h-4 w-4 rounded-full border border-border" />
            )}
            <span>{stage.label}</span>
          </div>
        );
      })}
    </div>
  );
}
