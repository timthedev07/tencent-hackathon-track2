'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  AlertTriangle,
  ArrowRight,
  Check,
  Clipboard,
  Clock3,
  FileSearch,
  History,
  Loader2,
  MessageSquareText,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { Citation } from '@/components/Citation';
import { QueryProgress } from '@/components/QueryProgress';
import { GlassPanel } from '@/components/GlassPanel';
import { SseStream, type SseEventType } from '@/lib/sse/stream';
import {
  ApiClientError,
  createQuery,
  getConnections,
  getQueryResult,
  type Citation as CitationType,
  type ConnectionStatus,
  type QueryResult,
} from '@/lib/api/client';

type ViewState = 'idle' | 'streaming' | 'complete' | 'failed';
const TERMINAL_QUERY_STATUSES = new Set(['completed', 'partial', 'denied', 'failed']);

const SUGGESTIONS = [
  'What did the team decide about the Q4 migration plan?',
  'Summarise the latest product launch risks.',
  'Which decisions are waiting on my team?',
];

const sourceLabels: Record<string, string> = { confluence: 'Confluence', slack: 'Slack', google_drive: 'Google Drive' };

export default function QueryPage() {
  const [query, setQuery] = useState('');
  const [viewState, setViewState] = useState<ViewState>('idle');
  const [currentStage, setCurrentStage] = useState<SseEventType | null>(null);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connections, setConnections] = useState<ConnectionStatus[]>([]);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    getConnections().then((data) => setConnections(data.connections)).catch(() => setConnections([]));
  }, []);

  const loadResult = useCallback(async (queryId: string): Promise<boolean> => {
    try {
      const res = await getQueryResult(queryId);
      if (!TERMINAL_QUERY_STATUSES.has(res.status)) return false;
      setResult(res);
      if (res.status === 'failed') {
        setViewState('failed');
        setError(res.errorMessage ?? `The search failed (${res.errorCode ?? 'PIPELINE_ERROR'}).`);
      } else {
        setViewState('complete');
      }
      return true;
    } catch (e) {
      setViewState('failed');
      setError(e instanceof ApiClientError ? e.message : 'Failed to load the answer.');
      return true;
    }
  }, []);

  const handleSubmit = useCallback(async () => {
    if (!query.trim()) return;
    setViewState('streaming');
    setCurrentStage(null);
    setResult(null);
    setError(null);
    setCopied(false);
    try {
      const { queryId } = await createQuery(query.trim());
      const sse = new SseStream(queryId, {
        onEvent: (event) => {
          if (['query.completed', 'query.partial', 'query.denied'].includes(event.event)) {
            void loadResult(queryId);
            return;
          }
          if (event.event === 'query.failed') {
            setViewState('failed');
            setError(event.data.message ?? 'The query could not be completed. Check the server log using the query ID.');
            return;
          }
          setCurrentStage(event.event);
        },
        onError: (sseError) => {
          void loadResult(queryId).then((queryIsTerminal) => {
            if (!queryIsTerminal) {
              setViewState('failed');
              setError(sseError.message);
            }
          });
        },
      });
      sse.connect();
    } catch (e) {
      setViewState('failed');
      setError(e instanceof ApiClientError ? e.message : 'Failed to submit the query.');
    }
  }, [query, loadResult]);

  const connectedSources = connections.filter((connection) => connection.connected);
  const answerReady = viewState === 'complete' && result;
  const hasNoSearchableContent = result?.errorCode === 'NO_SEARCHABLE_CONTENT';
  const copyAnswer = async () => {
    if (!result?.answer) return;
    await navigator.clipboard.writeText(result.answer);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  const progressLabel = useMemo(() => {
    const labels: Partial<Record<SseEventType, string>> = {
      'query.authenticated': 'Authenticating your request',
      'query.retrieving': 'Searching your connected sources',
      'query.reauthorising': 'Checking your current access',
      'query.reranking': 'Ranking authorised evidence',
      'query.generating': 'Writing a grounded answer',
      'query.verifying': 'Verifying citations',
      'query.auditing': 'Saving the audit record',
    };
    return currentStage ? labels[currentStage] ?? 'Finishing your answer' : 'Starting a secure search';
  }, [currentStage]);

  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-10 flex flex-wrap items-end justify-between gap-5">
        <div><div className="section-kicker flex items-center gap-2"><Sparkles size={13} /> Intelligent workspace search</div><h1 className="page-title">What can I help you find?</h1><p className="page-subtitle">Ask naturally. Relay will search across the sources you have connected and return only evidence you are allowed to see.</p></div>
        <Link href="/settings" className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs font-medium ghost-glass"><SettingsIcon /> Manage sources <ArrowRight size={14} /></Link>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-5">
          <GlassPanel className="p-5 md:p-7" intensity="strong">
            <div className="mb-4 flex items-center justify-between"><div className="flex items-center gap-2"><span className="grid h-8 w-8 place-items-center rounded-xl bg-violet-300/10 text-violet-200"><MessageSquareText size={16} /></span><span className="section-title">New question</span></div><span className="section-kicker">Private to your workspace</span></div>
            <textarea className="glass-input min-h-[142px] resize-none p-4 text-base leading-7" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Ask anything about your connected sources…" disabled={viewState === 'streaming'} onKeyDown={(event) => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') handleSubmit(); }} aria-label="Your question" />
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3"><span className="text-[11px] text-white/35">Press ⌘ Enter to ask</span><button className="inline-flex h-10 items-center gap-2 rounded-xl px-4 text-xs font-semibold primary-glow" onClick={handleSubmit} disabled={viewState === 'streaming' || !query.trim()}>{viewState === 'streaming' ? <><Loader2 size={15} className="animate-spin" /> Searching…</> : <><Send size={15} /> Ask Relay</>}</button></div>
          </GlassPanel>

          {viewState === 'idle' && <GlassPanel className="p-5" intensity="soft"><div className="mb-3 flex items-center gap-2 text-xs font-semibold text-white/75"><Sparkles size={14} className="text-violet-200" /> Start with a prompt</div><div className="grid gap-2 md:grid-cols-3">{SUGGESTIONS.map((suggestion) => <button key={suggestion} onClick={() => setQuery(suggestion)} className="rounded-xl border border-white/8 bg-white/[.035] px-3 py-3 text-left text-xs leading-5 text-white/55 transition hover:border-violet-200/25 hover:bg-white/[.07] hover:text-white/80">{suggestion}<ArrowRight size={13} className="mt-2 text-white/30" /></button>)}</div></GlassPanel>}

          {viewState === 'streaming' && <GlassPanel className="p-5 md:p-7" intensity="soft"><div className="mb-5 flex items-start justify-between"><div><div className="section-kicker">In progress</div><h2 className="mt-2 text-lg font-semibold text-white">{progressLabel}</h2><p className="mt-1 text-xs text-white/40">Your answer is being assembled from authorised evidence.</p></div><div className="grid h-10 w-10 place-items-center rounded-lg bg-violet-300/10 text-violet-200"><Loader2 size={18} className="animate-spin" /></div></div><QueryProgress currentStage={currentStage} isComplete={false} hasFailed={false} /></GlassPanel>}

          {viewState === 'failed' && <GlassPanel className="p-6" intensity="soft"><div className="flex items-start gap-4"><div className="grid h-10 w-10 flex-none place-items-center rounded-xl bg-rose-300/10 text-rose-200"><AlertTriangle size={18} /></div><div><h2 className="text-sm font-semibold text-white">We couldn&apos;t finish that search</h2><p className="mt-2 text-xs leading-5 text-white/55">{error ?? 'Something went wrong while contacting the assistant.'}</p><button className="mt-4 inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium ghost-glass" onClick={handleSubmit}><RefreshCw size={13} /> Try again</button></div></div></GlassPanel>}

          {answerReady && result && <div className="space-y-5">
            {result.partialSourceWarnings.length > 0 && <GlassPanel className="flex items-start gap-3 p-4" intensity="soft"><AlertTriangle size={17} className="mt-0.5 flex-none text-amber-200" /><div><p className="text-xs font-semibold text-amber-100">Some sources were unavailable</p><p className="mt-1 text-xs leading-5 text-white/55">Answer generated from sources that could be verified. {result.partialSourceWarnings.map((warning) => `${sourceLabels[warning.source] ?? warning.source} could not be checked in time, so its content was excluded.`).join(' ')}</p></div></GlassPanel>}
            {hasNoSearchableContent ? <GlassPanel className="p-6" intensity="soft"><div className="flex items-start gap-4"><div className="grid h-10 w-10 flex-none place-items-center rounded-xl bg-violet-300/10 text-violet-200"><FileSearch size={18} /></div><div><h2 className="text-sm font-semibold text-white">No searchable content yet</h2><p className="mt-2 max-w-xl text-xs leading-5 text-white/55">Your connected workspaces are currently empty, so Relay has nothing to search. Add content and allow the initial sync to finish, then ask again.</p><Link href="/settings" className="mt-4 inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium ghost-glass">Manage sources <ArrowRight size={13} /></Link></div></div></GlassPanel> : result.answer ? <GlassPanel className="p-6 md:p-8" intensity="strong"><div className="mb-6 flex flex-wrap items-center justify-between gap-3"><div><div className="section-kicker flex items-center gap-2"><ShieldCheck size={13} /> Verified answer</div><h2 className="mt-2 text-xl font-semibold tracking-tight text-white">Here&apos;s what I found</h2></div><div className="flex gap-2"><button className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs ghost-glass" onClick={copyAnswer}>{copied ? <Check size={13} /> : <Clipboard size={13} />} {copied ? 'Copied' : 'Copy'}</button><Link href={`/audit/${result.queryId}`} className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs ghost-glass"><FileSearch size={13} /> Audit trail</Link></div></div><p className="whitespace-pre-wrap text-[15px] leading-8 text-white/80">{result.answer}</p><div className="mt-7 flex flex-wrap items-center gap-2 border-t border-white/8 pt-4 text-[11px] text-white/35"><Check size={13} className="text-teal-300" /> Answer committed after citation and audit verification</div></GlassPanel> : <GlassPanel className="p-6" intensity="soft"><div className="text-sm font-semibold text-white">No answer available</div><p className="mt-2 text-xs leading-5 text-white/50">{result.status === 'denied' ? 'You do not currently have access to any relevant sources.' : 'The query did not produce an answer.'}</p></GlassPanel>}
            {result.citations.length > 0 && <section><div className="mb-3 flex items-end justify-between"><div><div className="section-title">Evidence used</div><div className="mt-1 section-kicker">Open a citation to check access again before viewing its text.</div></div><span className="source-chip"><ShieldCheck size={12} /> {result.citations.length} verified</span></div><div className="space-y-3">{result.citations.map((citation: CitationType) => <Citation key={citation.evidenceId} citation={citation} />)}</div></section>}
          </div>}
        </div>

        <aside className="space-y-4">
          <GlassPanel className="p-5" intensity="soft"><div className="flex items-center justify-between"><div className="flex items-center gap-2"><span className="grid h-8 w-8 place-items-center rounded-xl bg-teal-300/10 text-teal-200"><ShieldCheck size={15} /></span><span className="section-title">Your sources</span></div><Link href="/settings" aria-label="Manage sources"><ArrowRight size={15} className="text-white/40" /></Link></div><p className="mt-4 text-xs leading-5 text-white/45">Relay will only search sources you have explicitly connected.</p><div className="mt-4 space-y-2">{connectedSources.length > 0 ? connectedSources.map((source) => <div key={source.source} className="flex items-center justify-between rounded-xl border border-white/7 bg-white/[.03] px-3 py-2.5"><span className="flex items-center gap-2 text-xs text-white/70"><span className={`source-dot source-dot-${source.source === 'google_drive' ? 'google' : source.source}`} /> {sourceLabels[source.source] ?? source.source}</span><span className="flex items-center gap-1 text-[10px] text-teal-200"><span className="status-pulse" /> Connected</span></div>) : <div className="rounded-xl border border-dashed border-white/10 p-3 text-xs leading-5 text-white/40">No sources connected yet. Connect one to unlock workspace search.</div>}</div></GlassPanel>
          <GlassPanel className="p-5" intensity="soft"><div className="flex items-center gap-2"><History size={15} className="text-violet-200" /><span className="section-title">Built for trust</span></div><div className="mt-4 space-y-3"><TrustPoint icon={<ShieldCheck size={14} />} title="Live access checks" copy="Access is checked again when you open evidence." /><TrustPoint icon={<Clock3 size={14} />} title="Fresh enough to act" copy="Source revisions remain immutable and traceable." /><TrustPoint icon={<FileSearch size={14} />} title="Evidence first" copy="Every answer is grounded in labelled citations." /></div></GlassPanel>
        </aside>
      </div>
    </div>
  );
}

function TrustPoint({ icon, title, copy }: { icon: React.ReactNode; title: string; copy: string }) { return <div className="flex gap-3"><span className="mt-0.5 text-violet-200">{icon}</span><div><p className="text-xs font-medium text-white/75">{title}</p><p className="mt-1 text-[11px] leading-5 text-white/40">{copy}</p></div></div>; }
function SettingsIcon() { return <span className="text-violet-200"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/><path d="m19.4 15 .1.1a2 2 0 1 1-2.8 2.8l-.1-.1a2 2 0 0 0-3.4 1.4v.3a2 2 0 1 1-4 0v-.2a2 2 0 0 0-3.4-1.4l-.1.1A2 2 0 1 1 3 15.2l.1-.1A2 2 0 0 0 1.7 11.7h-.2a2 2 0 1 1 0-4h.2A2 2 0 0 0 3 4.3l-.1-.1A2 2 0 1 1 5.7 1.4l.1.1A2 2 0 0 0 9.2.1V0a2 2 0 1 1 4 0v.2a2 2 0 0 0 3.4 1.4l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a2 2 0 0 0 1.4 3.4h.2a2 2 0 1 1 0 4h-.2a2 2 0 0 0-1.4 3.4Z"/></svg></span>; }
