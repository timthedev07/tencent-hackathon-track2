'use client';

import { useState } from 'react';
import { ExternalLink, Eye, EyeOff, Lock, ShieldCheck } from 'lucide-react';
import { GlassPanel } from '@/components/GlassPanel';
import { ApiClientError, getEvidence, type Citation as CitationType } from '@/lib/api/client';

const SOURCE_LABELS: Record<string, string> = { confluence: 'Confluence', jira: 'Jira', slack: 'Slack', google_drive: 'Google Drive' };

export function Citation({ citation }: { citation: CitationType }) {
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [evidenceText, setEvidenceText] = useState<string | null>(null);
  const [accessRevoked, setAccessRevoked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleOpen() {
    if (isOpen) { setIsOpen(false); return; }
    setIsLoading(true); setError(null); setAccessRevoked(false); setEvidenceText(null);
    try {
      const result = await getEvidence(citation.evidenceId);
      if (result.status === 'access_revoked') setAccessRevoked(true);
      else { setEvidenceText(result.quotedSpan ?? null); setIsOpen(true); }
    } catch (e) {
      if (e instanceof ApiClientError && e.status === 403) setAccessRevoked(true);
      else setError(e instanceof ApiClientError ? e.message : 'Unable to load evidence.');
    } finally { setIsLoading(false); }
  }

  return (
    <GlassPanel className="p-4" intensity="soft" interactive>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1"><div className="mb-2 flex flex-wrap items-center gap-2"><span className={`source-dot source-dot-${citation.source === 'google_drive' ? 'google' : citation.source}`} /><span className="text-[11px] font-semibold text-white/55">{SOURCE_LABELS[citation.source] ?? citation.source}</span><span className="text-[10px] text-white/25">Verified source</span></div><p className="truncate text-sm font-medium text-white/80">{citation.label ?? 'Untitled evidence'}</p>{citation.url && <a href={citation.url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 text-[11px] text-violet-200/70 hover:text-violet-100"><ExternalLink size={11} /> Open original source</a>}</div>
        <button className="inline-flex flex-none items-center gap-2 rounded-lg px-3 py-2 text-[11px] font-medium ghost-glass" onClick={handleOpen} disabled={isLoading}>{isLoading ? 'Checking…' : isOpen ? <><EyeOff size={13} /> Hide text</> : <><Eye size={13} /> View evidence</>}</button>
      </div>
      {accessRevoked && <div className="mt-4 flex items-start gap-3 rounded-xl border border-amber-200/10 bg-amber-300/[.06] p-3 text-xs text-white/55"><Lock size={15} className="mt-0.5 flex-none text-amber-200" /><span><strong className="font-semibold text-amber-100">Access revoked.</strong> You no longer have access to this content. It was rechecked when you opened the citation.</span></div>}
      {error && <p className="mt-3 text-xs text-rose-200">{error}</p>}
      {isOpen && evidenceText && <div className="mt-4 rounded-lg border border-teal-200/10 bg-teal-300/[.045] p-4"><div className="mb-2 flex items-center gap-2 text-xs font-semibold text-teal-200/75"><ShieldCheck size={12} /> Authorised evidence</div><p className="rounded-md border border-teal-200/20 p-3 text-sm leading-6 text-white/70">{evidenceText}</p></div>}
    </GlassPanel>
  );
}
