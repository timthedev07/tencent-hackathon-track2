'use client';

import { useState, useEffect, useCallback, useRef, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { CheckCircle2, Cloud, ExternalLink, FileText, Link2, Loader2, MessageCircle, RefreshCw, ShieldCheck, XCircle } from 'lucide-react';
import { GlassPanel } from '@/components/GlassPanel';
import { ApiClientError, getAuthToken, getBackfillPreviews, getBackfillStatus, getConnections, startOAuth, triggerManualBackfill, type BackfillPreviewItem, type BackfillPreviewResponse, type BackfillRunStatus, type ConnectionStatus } from '@/lib/api/client';

const PROVIDERS = [
  { label: 'Atlassian', key: 'atlassian' as const, source: 'confluence', description: 'Confluence pages and Jira project knowledge', icon: Link2, tint: 'text-blue-200', dot: 'source-dot-confluence' },
  { label: 'Slack', key: 'slack' as const, source: 'slack', description: 'Threads and conversations you can access', icon: MessageCircle, tint: 'text-pink-200', dot: 'source-dot-slack' },
  { label: 'Google Drive', key: 'google' as const, source: 'google_drive', description: 'Documents and shared workspace files', icon: Cloud, tint: 'text-emerald-200', dot: 'source-dot-google' },
];

function SettingsContent() {
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [connections, setConnections] = useState<Record<string, ConnectionStatus>>({});
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [syncRuns, setSyncRuns] = useState<Record<string, BackfillRunStatus>>({});
  const [previews, setPreviews] = useState<BackfillPreviewResponse | null>(null);
  const [refreshingPreviews, setRefreshingPreviews] = useState(false);
  const activeRunIds = useRef(new Set<string>());

  const refreshConnections = useCallback(async () => {
    try {
      const data = await getConnections();
      setConnections(Object.fromEntries(data.connections.map((connection) => [connection.source, connection])));
    } catch (e) {
      if (e instanceof ApiClientError && e.status === 401) setError('Please sign in before connecting sources.');
    } finally { setLoading(false); }
  }, []);

  const refreshPreviews = useCallback(async () => {
    setRefreshingPreviews(true);
    try { setPreviews(await getBackfillPreviews()); }
    catch (e) { if (e instanceof ApiClientError && e.status === 401) setError('Please sign in before viewing imported items.'); }
    finally { setRefreshingPreviews(false); }
  }, []);

  const refreshBackfillStatus = useCallback(async () => {
    try {
      const data = await getBackfillStatus();
      const completedNow = data.runs.some((run) => activeRunIds.current.has(run.id) && !isActiveRun(run));
      activeRunIds.current = new Set(data.runs.filter(isActiveRun).map((run) => run.id));
      setSyncRuns(Object.fromEntries(data.runs.map((run) => [run.source, run])));
      if (completedNow) {
        void refreshPreviews();
        setSuccess('Import complete. The latest authorised items are shown below.');
      }
    } catch (e) {
      if (e instanceof ApiClientError && e.status === 401) setError('Please sign in to view import progress.');
    }
  }, [refreshPreviews]);

  useEffect(() => {
    const refresh = window.setTimeout(() => { void refreshConnections(); void refreshPreviews(); void refreshBackfillStatus(); }, 0);
    return () => window.clearTimeout(refresh);
  }, [refreshBackfillStatus, refreshConnections, refreshPreviews]);
  useEffect(() => {
    if (!Object.values(syncRuns).some(isActiveRun)) return;
    const poll = window.setInterval(() => { void refreshBackfillStatus(); }, 2000);
    return () => window.clearInterval(poll);
  }, [refreshBackfillStatus, syncRuns]);
  useEffect(() => {
    const status = searchParams.get('status'); const source = searchParams.get('source'); const errorMsg = searchParams.get('error');
    if (!source) return;
    const label = PROVIDERS.find((provider) => provider.source === source)?.label ?? source;
    const update = window.setTimeout(() => {
      if (status === 'connected') { setSuccess(`${label} connected successfully.`); void refreshConnections(); }
      if (status === 'error') setError(`${label} connection failed${errorMsg ? `: ${decodeURIComponent(errorMsg)}` : ''}`);
    }, 0);
    return () => window.clearTimeout(update);
  }, [searchParams, refreshConnections]);

  async function handleConnect(provider: 'atlassian' | 'slack' | 'google', label: string) {
    setError(null); setSuccess(null); setConnecting(label);
    try { const { authoriseUrl } = await startOAuth(provider); window.location.assign(authoriseUrl); }
    catch (e) { setError(e instanceof ApiClientError ? e.message : `Failed to start ${label} connection.`); setConnecting(null); }
  }

  async function handleSync(provider: 'atlassian' | 'slack' | 'google_drive', label: string, source: string) {
    setError(null); setSuccess(null); setSyncing(source);
    try {
      const result = await triggerManualBackfill(provider);
      activeRunIds.current = new Set(result.runs.map((run) => run.id));
      setSyncRuns((current) => ({ ...current, ...Object.fromEntries(result.runs.map((run) => [run.source, run])) }));
      setSuccess(`${label}: ${result.message}`);
      void refreshBackfillStatus();
    } catch (e) {
      setError(e instanceof ApiClientError ? e.message : `Failed to queue the ${label} import.`);
    } finally { setSyncing(null); }
  }

  const hasToken = Boolean(getAuthToken());
  const connectedCount = PROVIDERS.filter((provider) => connections[provider.source]?.connected).length;

  if (loading) return <SettingsLoading />;

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-10 flex flex-wrap items-end justify-between gap-5"><div><div className="section-kicker flex items-center gap-2"><ShieldCheck size={13} /> Trust centre</div><h1 className="page-title">Your connected sources.</h1><p className="page-subtitle">Choose the places Relay can search. Connections are delegated to your identity and checked again at query time.</p></div><div className="flex items-center gap-2"><button className="inline-flex h-9 items-center gap-2 rounded-lg px-3 text-xs font-medium ghost-glass disabled:opacity-40" onClick={() => void refreshPreviews()} disabled={!hasToken || refreshingPreviews}>{refreshingPreviews ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Refresh imported items</button><div className="flex items-center gap-2 rounded-full border border-teal-200/10 bg-teal-300/[.06] px-3 py-2 text-xs text-teal-100"><span className="status-pulse" /> {connectedCount} of {PROVIDERS.length} connected</div></div></div>

      {!hasToken && <GlassPanel className="mb-5 flex items-start gap-3 p-4" intensity="soft"><ShieldCheck size={17} className="mt-0.5 flex-none text-amber-200" /><div><p className="text-xs font-semibold text-amber-100">Sign in to manage connections</p><p className="mt-1 text-xs text-white/50">Relay needs your enterprise identity before it can link a source.</p><a href="/login" className="mt-3 inline-flex text-xs font-medium text-violet-200 hover:text-violet-100">Go to sign in <span className="ml-1">→</span></a></div></GlassPanel>}
      {success && <Notice icon={<CheckCircle2 size={16} />} tone="success" message={success} />}
      {error && <Notice icon={<XCircle size={16} />} tone="error" message={error} />}

      <div className="grid gap-4 lg:grid-cols-3">
        {PROVIDERS.map((provider) => {
          const connection = connections[provider.source]; const connected = connection?.connected ?? false; const Icon = provider.icon;
          const syncProvider = provider.key === 'google' ? 'google_drive' : provider.key;
          const syncRun = providerRun(provider.key, syncRuns);
          const syncActive = syncRun ? isActiveRun(syncRun) : false;
          const preview = provider.key === 'atlassian' ? <AtlassianPreview preview={previews?.atlassian} /> : <PreviewList heading={provider.key === 'slack' ? 'Latest messages' : 'Last edited files'} items={provider.key === 'slack' ? previews?.slack : previews?.googleDrive} />;
          return <GlassPanel key={provider.source} className="flex min-h-[260px] flex-col p-6" intensity={connected ? 'strong' : 'soft'} interactive><div className="flex items-start justify-between"><div className={`grid h-11 w-11 place-items-center rounded-lg border border-white/10 bg-white/[.05] ${provider.tint}`}><Icon size={20} /></div><span className={`inline-flex items-center gap-1.5 text-xs font-medium ${connected ? 'text-teal-200' : 'text-white/35'}`}><span className={`source-dot ${provider.dot}`} /> {connected ? 'Connected' : 'Not connected'}</span></div><h2 className="mt-7 text-base font-semibold text-white">{provider.label}</h2><p className="mt-2 text-xs leading-5 text-white/45">{provider.description}</p>{connected && <p className="mt-3 truncate text-[11px] text-white/60">{connection.display_name ?? 'Identity linked'}</p>}{connected && <SyncProgress run={syncRun} />}{connected && preview}<div className="mt-auto space-y-2 pt-5">{connected && <button className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-violet-300/10 text-xs font-medium text-violet-100 transition hover:bg-violet-300/15 disabled:cursor-not-allowed disabled:opacity-50" onClick={() => handleSync(syncProvider, provider.label, provider.source)} disabled={!hasToken || syncing === provider.source || syncActive}>{syncing === provider.source ? <><Loader2 size={14} className="animate-spin" /> Queueing import…</> : syncActive ? <><Loader2 size={14} className="animate-spin" /> {syncRun?.status === 'scanning' ? 'Scanning source…' : `Importing ${syncRun?.processedCount ?? 0}/${syncRun?.enqueuedCount || '…'}`}</> : <><RefreshCw size={14} /> {syncRun?.status === 'completed' ? 'Sync again' : 'Sync now'}</>}</button>}<button className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg text-xs font-medium ghost-glass disabled:opacity-40" onClick={() => handleConnect(provider.key, provider.label)} disabled={!hasToken || connecting === provider.label}>{connecting === provider.label ? <><Loader2 size={14} className="animate-spin" /> Connecting…</> : <><ExternalLink size={14} /> {connected ? `Reconnect ${provider.label}` : `Connect ${provider.label}`}</>}</button></div></GlassPanel>;
        })}
      </div>

      <GlassPanel className="mt-5 flex items-start gap-4 p-5" intensity="soft"><div className="grid h-9 w-9 flex-none place-items-center rounded-xl bg-violet-300/10 text-violet-200"><FileText size={16} /></div><div><h2 className="text-sm font-semibold text-white">How Relay keeps this safe</h2><p className="mt-2 max-w-2xl text-xs leading-6 text-white/50">We use delegated access from your provider account. Search candidates are filtered locally, evidence is re-authorised live, and audit records are committed before an answer is shown.</p></div></GlassPanel>
    </div>
  );
}

function Notice({ icon, tone, message }: { icon: React.ReactNode; tone: 'success' | 'error'; message: string }) { return <div className={`mb-5 flex items-center gap-3 rounded-xl border p-3 text-xs ${tone === 'success' ? 'border-teal-200/10 bg-teal-300/[.06] text-teal-100' : 'border-rose-200/10 bg-rose-300/[.06] text-rose-100'}`}>{icon}<span>{message}</span></div>; }
function isActiveRun(run: BackfillRunStatus): boolean { return run.status === 'queued' || run.status === 'scanning' || run.status === 'processing'; }
function providerRun(provider: 'atlassian' | 'slack' | 'google', runs: Record<string, BackfillRunStatus>): BackfillRunStatus | undefined {
  const sources = provider === 'atlassian' ? ['confluence', 'jira'] : [provider === 'google' ? 'google_drive' : 'slack'];
  const sourceRuns = sources.map((source) => runs[source]).filter((run): run is BackfillRunStatus => Boolean(run));
  if (sourceRuns.length === 0) return undefined;
  const active = sourceRuns.find(isActiveRun);
  const failed = sourceRuns.every((run) => run.status === 'failed');
  const latest = sourceRuns[0];
  return {
    ...latest,
    status: active?.status ?? (failed ? 'failed' : 'completed'),
    discoveredCount: sourceRuns.reduce((total, run) => total + run.discoveredCount, 0),
    enqueuedCount: sourceRuns.reduce((total, run) => total + run.enqueuedCount, 0),
    processedCount: sourceRuns.reduce((total, run) => total + run.processedCount, 0),
    failedCount: sourceRuns.reduce((total, run) => total + run.failedCount, 0),
    errorMessage: sourceRuns.find((run) => run.errorMessage)?.errorMessage ?? null,
  };
}
function SyncProgress({ run }: { run: BackfillRunStatus | undefined }) {
  if (!run) return null;
  if (run.status === 'failed') return <p className="mt-3 rounded-lg border border-rose-200/10 bg-rose-300/[.06] px-2.5 py-2 text-[11px] leading-4 text-rose-100">Import failed{run.errorMessage ? `: ${run.errorMessage}` : '. Try syncing again.'}</p>;
  if (isActiveRun(run)) {
    const label = run.status === 'queued' ? 'Waiting for worker…' : run.status === 'scanning' ? 'Scanning source…' : `Indexed ${run.processedCount} of ${run.enqueuedCount} items`;
    return <div className="mt-3 rounded-lg border border-violet-200/10 bg-violet-300/[.06] px-2.5 py-2"><div className="flex items-center gap-2 text-[11px] text-violet-100"><Loader2 size={12} className="animate-spin" /> {label}</div>{run.enqueuedCount > 0 && <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-violet-200 transition-all" style={{ width: `${Math.min(100, (run.processedCount / run.enqueuedCount) * 100)}%` }} /></div>}</div>;
  }
  return <p className="mt-3 text-[11px] text-teal-100">Last import complete: {run.processedCount} of {run.enqueuedCount} items indexed{run.failedCount ? `, ${run.failedCount} failed` : ''}.</p>;
}
function AtlassianPreview({ preview }: { preview: BackfillPreviewResponse['atlassian'] | undefined }) { return <div className="mt-5 space-y-3"><PreviewList heading="Latest pages" items={preview?.pages} /><PreviewList heading="Latest tickets" items={preview?.tickets} /></div>; }
function PreviewList({ heading, items }: { heading: string; items: BackfillPreviewItem[] | undefined }) { return <section><p className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/35">{heading}</p>{items === undefined ? <p className="text-[11px] text-white/35">Checking imported items…</p> : items.length === 0 ? <p className="text-[11px] text-white/35">No indexed items yet.</p> : <ul className="space-y-1.5">{items.map((item) => <li key={`${item.source}-${item.url ?? item.title}`} className="rounded-lg border border-white/[.06] bg-white/[.025] px-2.5 py-2"><div className="flex items-start gap-2"><div className="min-w-0 flex-1">{item.url ? <a href={item.url} target="_blank" rel="noreferrer" className="block truncate text-[11px] font-medium text-violet-100 hover:text-violet-50">{item.title}</a> : <p className="truncate text-[11px] font-medium text-white/75">{item.title}</p>}{item.snippet && <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-white/45">{item.snippet}</p>}{item.updatedAt && <p className="mt-1 text-[10px] text-white/30">Updated {formatPreviewDate(item.updatedAt)}</p>}</div>{item.url && <ExternalLink size={12} className="mt-0.5 flex-none text-white/30" />}</div></li>)}</ul>}</section>; }
function formatPreviewDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? 'recently' : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }); }
function SettingsLoading() { return <div className="mx-auto max-w-5xl"><div className="h-3 w-24 animate-pulse rounded bg-white/10" /><div className="mt-4 h-12 w-96 max-w-full animate-pulse rounded bg-white/10" /><div className="mt-10 grid gap-4 md:grid-cols-3">{[1, 2, 3].map((item) => <div key={item} className="h-64 animate-pulse rounded-3xl border border-white/5 bg-white/[.03]" />)}</div><div className="mt-8 flex items-center gap-2 text-xs text-white/35"><RefreshCw size={14} className="animate-spin" /> Checking connections…</div></div>; }

export default function SettingsPage() { return <Suspense fallback={<SettingsLoading />}><SettingsContent /></Suspense>; }
