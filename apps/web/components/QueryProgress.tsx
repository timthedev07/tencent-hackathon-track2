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
    <div className="flex flex-col gap-2">
      {STAGES.map((stage, index) => {
        const isDone = isComplete || index < currentIndex;
        const isActive = index === currentIndex && !isComplete && !hasFailed;
        return (
          <div
            key={stage.event}
            className={cn(
              'flex items-center gap-3 rounded-xl border border-transparent px-3 py-2.5 text-xs transition',
              isActive && 'border-violet-200/15 bg-violet-300/[.08] font-medium text-white',
              isDone && 'text-teal-200',
              !isActive && !isDone && 'text-white/30',
              hasFailed && index >= currentIndex && 'text-white/30',
            )}
          >
            {isDone ? (
              <CheckCircle2 className="h-4 w-4 text-teal-300" />
            ) : isActive ? (
              <Loader2 className="h-4 w-4 animate-spin text-violet-200" />
            ) : (
              <div className="h-4 w-4 rounded-full border border-white/15" />
            )}
            <span>{stage.label}</span>
          </div>
        );
      })}
    </div>
  );
}
