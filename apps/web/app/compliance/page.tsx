'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { ArrowRight, FileSearch, ShieldAlert, ShieldCheck, UserRound } from 'lucide-react';
import { GlassPanel } from '@/components/GlassPanel';
import { ApiClientError, AuditHistoryItem, getAuditHistory } from '@/lib/api/client';

const PAGE_SIZE = 25;

function formatTimestamp(value: string | null): string {
  if (!value) return 'Unknown time';
  return new Intl.DateTimeFormat('en-GB', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value));
}

function statusLabel(status: string): string {
  return status.replaceAll('_', ' ');
}

function HistoryRow({ item }: { item: AuditHistoryItem }) {
  const statusTone = item.queryStatus === 'failed' || item.queryStatus === 'denied'
    ? 'text-rose-200'
    : item.queryStatus === 'verified' || item.queryStatus === 'audited'
      ? 'text-teal-200'
      : 'text-white/55';

  return (
    <div className="rounded-xl border border-white/8 bg-white/[.025] p-4 transition hover:border-violet-200/20 hover:bg-white/[.045]">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium leading-6 text-white/85">{item.queryText}</p>
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px] text-white/40">
            <span>{formatTimestamp(item.timestamp)}</span>
            <span className="flex items-center gap-1.5"><UserRound size={12} /> {item.userDisplayName ?? item.userIdentifier ?? 'Unknown user'}</span>
            <span>{item.evidenceCount} evidence items</span>
            <span>{item.auditEventCount} audit events</span>
          </div>
        </div>
        <div className="flex flex-none items-center gap-3">
          <span className={`text-[11px] font-medium capitalize ${statusTone}`}>{statusLabel(item.queryStatus)}</span>
          <Link href={`/audit/${item.queryId}`} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-[11px] font-medium ghost-glass">
            Open trail <ArrowRight size={13} />
          </Link>
        </div>
      </div>
    </div>
  );
}

export default function CompliancePage() {
  const [items, setItems] = useState<AuditHistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [auditChainVerified, setAuditChainVerified] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAuditHistory(PAGE_SIZE, 0)
      .then((response) => {
        setItems(response.items);
        setTotal(response.total);
        setAuditChainVerified(response.auditChainVerified);
      })
      .catch((reason) => {
        setError(reason instanceof ApiClientError && reason.status === 403
          ? 'This page is available to compliance users only.'
          : 'The compliance history could not be loaded.');
      })
      .finally(() => setIsLoading(false));
  }, []);

  async function loadMore() {
    setIsLoadingMore(true);
    try {
      const response = await getAuditHistory(PAGE_SIZE, items.length);
      setItems((current) => [...current, ...response.items]);
      setTotal(response.total);
      setAuditChainVerified(response.auditChainVerified);
    } catch {
      setError('More compliance history could not be loaded.');
    } finally {
      setIsLoadingMore(false);
    }
  }

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl">
        <div className="h-3 w-32 animate-pulse rounded bg-white/10" />
        <div className="mt-5 h-12 w-80 max-w-full animate-pulse rounded bg-white/10" />
        <div className="mt-9 h-24 animate-pulse rounded-2xl bg-white/[.03]" />
        <div className="mt-3 h-24 animate-pulse rounded-2xl bg-white/[.03]" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-3xl">
        <GlassPanel className="p-8 text-center" intensity="soft">
          <ShieldAlert size={26} className="mx-auto text-rose-200" />
          <h1 className="mt-4 text-lg font-semibold text-white">Compliance history unavailable</h1>
          <p className="mx-auto mt-2 max-w-md text-xs leading-5 text-white/45">{error}</p>
          <Link href="/query" className="mt-6 inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs ghost-glass">
            Back to assistant <ArrowRight size={13} />
          </Link>
        </GlassPanel>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-9 flex flex-wrap items-end justify-between gap-5">
        <div>
          <div className="section-kicker flex items-center gap-2"><FileSearch size={13} /> Compliance workspace</div>
          <h1 className="page-title">Query history.</h1>
          <p className="page-subtitle">Review every workspace query and open its tamper-evident audit trail.</p>
        </div>
        <div className={`flex items-center gap-2 rounded-full border px-3 py-2 text-xs ${auditChainVerified ? 'border-teal-200/10 bg-teal-300/[.06] text-teal-100' : 'border-rose-200/10 bg-rose-300/[.06] text-rose-100'}`}>
          {auditChainVerified ? <ShieldCheck size={14} /> : <ShieldAlert size={14} />}
          {auditChainVerified ? 'Audit chain verified' : 'Audit chain requires review'}
        </div>
      </div>

      <GlassPanel className="p-5 md:p-6" intensity="strong">
        <div className="mb-5 flex items-end justify-between gap-4">
          <div>
            <h2 className="text-sm font-semibold text-white">All queries</h2>
            <p className="mt-1 text-xs text-white/40">{total} recorded {total === 1 ? 'query' : 'queries'} in this workspace.</p>
          </div>
          <span className="source-chip"><ShieldCheck size={12} /> Compliance access</span>
        </div>

        {items.length === 0 ? (
          <div className="rounded-xl border border-dashed border-white/10 p-8 text-center text-xs text-white/40">
            No query history has been recorded yet.
          </div>
        ) : (
          <div className="space-y-3">
            {items.map((item) => <HistoryRow key={item.queryId} item={item} />)}
          </div>
        )}

        {items.length < total && (
          <button className="mt-5 inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium ghost-glass" onClick={loadMore} disabled={isLoadingMore}>
            {isLoadingMore ? 'Loading…' : 'Load more'} <ArrowRight size={13} />
          </button>
        )}
      </GlassPanel>
    </div>
  );
}
