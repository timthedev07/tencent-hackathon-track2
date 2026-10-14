import Link from 'next/link';
import { Clock3, ExternalLink, FileSearch, Link2, Ticket } from 'lucide-react';
import type { Citation } from '@/lib/api/client';

type TokenKind = 'evidence' | 'jira' | 'date' | 'url';

type AnswerToken = {
  kind: TokenKind;
  value: string;
  href?: string;
  citation?: Citation;
};

const ISSUE_KEY = /\b[A-Z][A-Z0-9]{1,9}-\d+\b/g;
const TOKEN = /https?:\/\/[^\s<>()\]]+|\[?evi_[0-9a-f]{6,}\]?|\b[A-Z][A-Z0-9]{1,9}-\d+\b|\b\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?\b|\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}\b|\b(?:[01]?\d|2[0-3]):[0-5]\d(?:\s?(?:am|pm))?\b/gi;

function safeExternalUrl(value: string | null): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.toString() : undefined;
  } catch {
    return undefined;
  }
}

function shortEvidenceId(evidenceId: string): string {
  return `evi_${evidenceId.replaceAll('-', '').slice(0, 12)}`;
}

function issueKeys(value: string): string[] {
  return [...value.matchAll(ISSUE_KEY)].map(([key]) => key);
}

function buildJiraLinkResolver(citations: Citation[]): (key: string) => string | undefined {
  const links = new Map<string, string>();
  const jiraCitation = citations.find((citation) => citation.source === 'jira' && safeExternalUrl(citation.url));

  for (const citation of citations.filter((item) => item.source === 'jira')) {
    const href = safeExternalUrl(citation.url);
    if (!href) continue;
    for (const key of issueKeys(`${citation.label ?? ''} ${href}`)) links.set(key, href);
  }

  // A cited Jira issue reveals the workspace URL pattern, so references to
  // neighbouring issues can remain useful even when only one was cited.
  if (jiraCitation) {
    const sourceUrl = safeExternalUrl(jiraCitation.url);
    const sourceKey = sourceUrl && issueKeys(sourceUrl)[0];
    if (sourceUrl && sourceKey) {
      return (key) => links.get(key) ?? sourceUrl.replace(sourceKey, key);
    }
  }
  return (key) => links.get(key);
}

function tokenFor(value: string, evidence: Map<string, Citation>, jiraHref: (key: string) => string | undefined): AnswerToken | null {
  const evidenceReference = value.replace(/^\[|\]$/g, '');
  if (evidenceReference.startsWith('evi_')) {
    const citation = evidence.get(evidenceReference);
    return citation ? { kind: 'evidence', value: evidenceReference, citation } : null;
  }
  if (/^https?:\/\//i.test(value)) {
    const href = safeExternalUrl(value);
    return href ? { kind: 'url', value, href } : null;
  }
  if (/^[A-Z][A-Z0-9]{1,9}-\d+$/.test(value)) {
    return { kind: 'jira', value, href: jiraHref(value) };
  }
  return { kind: 'date', value };
}

function Badge({ token }: { token: AnswerToken }) {
  const shared = 'answer-badge inline-flex align-middle items-center justify-start gap-1.5 rounded-full border px-3.5 py-2 text-xs font-medium leading-none';
  if (token.kind === 'evidence' && token.citation) {
    return <Link href={`/evidence/${token.citation.evidenceId}`} className={`${shared} border-teal-200/25 bg-teal-300/[.09] text-teal-100 hover:bg-teal-300/[.16]`} title={`Open ${token.citation.label ?? 'evidence'}`}><FileSearch size={13} /> Evidence</Link>;
  }
  if (token.kind === 'jira') {
    const content = <><Ticket size={13} /> {token.value}</>;
    return token.href
      ? <a href={token.href} target="_blank" rel="noopener noreferrer" className={`${shared} border-sky-200/25 bg-sky-300/[.09] text-sky-100 hover:bg-sky-300/[.16]`}>{content}</a>
      : <span className={`${shared} border-sky-200/20 bg-sky-300/[.07] text-sky-100`}>{content}</span>;
  }
  if (token.kind === 'url' && token.href) {
    return <a href={token.href} target="_blank" rel="noopener noreferrer" className={`${shared} border-violet-200/25 bg-violet-300/[.09] text-violet-100 hover:bg-violet-300/[.16]`}><Link2 size={13} /> Link <ExternalLink size={11} /></a>;
  }
  return <span className={`${shared} border-amber-200/25 bg-amber-300/[.09] text-amber-100`}><Clock3 size={13} /> {token.value}</span>;
}

export function AnswerContent({ answer, citations }: { answer: string; citations: Citation[] }) {
  const evidence = new Map(citations.map((citation) => [shortEvidenceId(citation.evidenceId), citation]));
  const jiraHref = buildJiraLinkResolver(citations);
  const pieces: React.ReactNode[] = [];
  let cursor = 0;

  for (const match of answer.matchAll(TOKEN)) {
    const value = match[0];
    const index = match.index ?? 0;
    if (index > cursor) pieces.push(answer.slice(cursor, index));
    const token = tokenFor(value, evidence, jiraHref);
    pieces.push(token ? <Badge key={`${index}-${value}`} token={token} /> : value);
    cursor = index + value.length;
  }
  if (cursor < answer.length) pieces.push(answer.slice(cursor));

  return <div className="whitespace-pre-wrap text-[15px] leading-8 text-white/80">{pieces}</div>;
}
