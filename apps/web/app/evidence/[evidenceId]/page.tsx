'use client';

import { use, useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, ExternalLink, FileText, Lock, ShieldAlert, ShieldCheck } from 'lucide-react';
import { GlassPanel } from '@/components/GlassPanel';
import { ApiClientError, getEvidence, type EvidenceResponse } from '@/lib/api/client';

const sourceLabels: Record<string, string> = { confluence: 'Confluence', jira: 'Jira', slack: 'Slack', google_drive: 'Google Drive' };

export default function EvidencePage({ params }: { params: Promise<{ evidenceId: string }> }) {
  const { evidenceId } = use(params);
  const [evidence, setEvidence] = useState<EvidenceResponse | null>(null);
  const [accessRevoked, setAccessRevoked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  useEffect(() => { getEvidence(evidenceId).then((result) => { if (result.status === 'access_revoked') setAccessRevoked(true); else setEvidence(result); }).catch((e) => { if (e instanceof ApiClientError && e.status === 403) setAccessRevoked(true); else setError(e instanceof ApiClientError ? e.message : 'Failed to load evidence.'); }).finally(() => setIsLoading(false)); }, [evidenceId]);

  if (isLoading) return <div className="mx-auto max-w-3xl"><div className="h-72 animate-pulse rounded-3xl bg-white/[.03]" /></div>;
  if (accessRevoked) return <div className="mx-auto max-w-2xl"><GlassPanel className="p-9 text-center" intensity="soft"><div className="mx-auto grid h-14 w-14 place-items-center rounded-lg bg-amber-300/10 text-amber-200"><Lock size={23} /></div><div className="section-kicker mt-6">Live access check</div><h1 className="mt-3 text-xl font-semibold text-white">This evidence is no longer available.</h1><p className="mx-auto mt-3 max-w-md text-sm leading-6 text-white/45">Your access was revoked after the answer was generated. Relay has hidden the title and text to keep the permission boundary intact.</p><Link href="/query" className="mt-7 inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs ghost-glass"><ArrowLeft size={13} /> Back to query</Link></GlassPanel></div>;
  if (error || !evidence) return <div className="mx-auto max-w-2xl"><GlassPanel className="p-8 text-center" intensity="soft"><ShieldAlert size={24} className="mx-auto text-rose-200" /><h1 className="mt-4 text-lg font-semibold text-white">Evidence unavailable</h1><p className="mt-2 text-xs text-white/45">{error ?? 'Evidence not found.'}</p><Link href="/query" className="mt-6 inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs ghost-glass"><ArrowLeft size={13} /> Back to query</Link></GlassPanel></div>;

  return <div className="mx-auto max-w-3xl"><Link href="/query" className="mb-7 inline-flex items-center gap-2 text-xs text-white/45 hover:text-white"><ArrowLeft size={13} /> Back to query</Link><GlassPanel className="p-6 md:p-9" intensity="strong"><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2 text-xs text-white/55"><span className={`source-dot source-dot-${evidence.source === 'google_drive' ? 'google' : evidence.source}`} /> {sourceLabels[evidence.source ?? ''] ?? evidence.source}</div><span className="inline-flex items-center gap-1.5 text-xs text-teal-200"><ShieldCheck size={12} /> Access verified</span></div><h1 className="mt-7 text-2xl font-semibold tracking-tight text-white">{evidence.title ?? 'Untitled evidence'}</h1><div className="mt-7 rounded-lg border border-teal-200/10 bg-teal-300/[.045] p-5"><div className="mb-3 flex items-center gap-2 text-xs font-semibold text-teal-200/75"><FileText size={12} /> Quoted evidence</div><p className="whitespace-pre-wrap rounded-md border border-teal-200/20 p-4 text-sm leading-7 text-white/70">{evidence.quotedSpan ?? 'No content available.'}</p></div>{evidence.canonicalUrl && <a href={evidence.canonicalUrl} target="_blank" rel="noopener noreferrer" className="mt-6 inline-flex items-center gap-2 text-xs text-violet-200 hover:text-violet-100"><ExternalLink size={13} /> View original source</a>}</GlassPanel></div>;
}
