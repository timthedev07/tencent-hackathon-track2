import Link from 'next/link'
import { Clock3, ExternalLink, FileSearch, Link2, Ticket } from 'lucide-react'
import type { Citation } from '@/lib/api/client'

type TokenKind = 'evidence' | 'jira' | 'date' | 'url'

type AnswerToken = {
  kind: TokenKind
  value: string
  href?: string
  citation?: Citation
}

const DATE_OR_TIME =
  /^(?:\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?|\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}|(?:[01]?\d|2[0-3]):[0-5]\d(?:\s?(?:am|pm))?)$/i
const BASE_TOKEN_SOURCE = String.raw`https?:\/\/[^\s<>()\]]+|\[?evi_[0-9a-f]{6,}\]?|\b\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?\b|\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}\b|\b(?:[01]?\d|2[0-3]):[0-5]\d(?:\s?(?:am|pm))?\b`

function safeExternalUrl(value: string | null): string | undefined {
  if (!value) return undefined
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.toString() : undefined
  } catch {
    return undefined
  }
}

function shortEvidenceId(evidenceId: string): string {
  return `evi_${evidenceId.replaceAll('-', '').slice(0, 12)}`
}

function escapeForRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function tokenPattern(jiraKeys: Iterable<string>): RegExp {
  const exactKeys = [...jiraKeys]
    .filter(Boolean)
    .sort((left, right) => right.length - left.length)
    .map((key) => `(?<![A-Za-z0-9_-])${escapeForRegex(key)}(?![A-Za-z0-9_-])`)
  return new RegExp(exactKeys.length ? `${exactKeys.join('|')}|${BASE_TOKEN_SOURCE}` : BASE_TOKEN_SOURCE, 'gi')
}

function buildJiraLinkResolver(citations: Citation[]): (key: string) => string | undefined {
  const links = new Map<string, string>()

  for (const citation of citations.filter((item) => item.source === 'jira')) {
    const href = safeExternalUrl(citation.url)
    if (!href) continue
    if (citation.itemKey) links.set(citation.itemKey, href)
  }
  return (key) => links.get(key)
}

function tokenFor(
  value: string,
  evidence: Map<string, Citation>,
  jiraHref: (key: string) => string | undefined,
): AnswerToken | null {
  const evidenceReference = value.replace(/^\[|\]$/g, '')
  if (evidenceReference.startsWith('evi_')) {
    const citation = evidence.get(evidenceReference)
    return citation ? { kind: 'evidence', value: evidenceReference, citation } : null
  }
  if (/^https?:\/\//i.test(value)) {
    const href = safeExternalUrl(value)
    return href ? { kind: 'url', value, href } : null
  }
  const jiraLink = jiraHref(value)
  if (jiraLink) return { kind: 'jira', value, href: jiraLink }
  return DATE_OR_TIME.test(value) ? { kind: 'date', value } : null
}

function Badge({ token }: { token: AnswerToken }) {
  const shared =
    'answer-badge inline-flex align-middle items-center justify-start gap-1.5 rounded-full border px-2.5 py-1.5 text-xs font-medium leading-none'
  const interactive =
    'cursor-pointer transition-[transform,background-color,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:scale-[1.02] active:translate-y-0 active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950'
  if (token.kind === 'evidence' && token.citation) {
    return (
      <Link
        href={`/evidence/${token.citation.evidenceId}`}
        className={`${shared} ${interactive} border-teal-200/25 bg-teal-300/[.09] text-teal-100 hover:border-teal-100/45 hover:bg-teal-300/[.16] hover:shadow-[0_8px_22px_rgba(45,212,191,0.18)] focus-visible:ring-teal-200/70`}
        title={`Open ${token.citation.label ?? 'evidence'}`}
      >
        <FileSearch size={13} /> Evidence
      </Link>
    )
  }
  if (token.kind === 'jira' && token.href) {
    const content = (
      <>
        <Ticket size={13} /> {token.value}
      </>
    )
    return (
      <a
        href={token.href}
        target="_blank"
        rel="noopener noreferrer"
        className={`${shared} ${interactive} border-sky-200/25 bg-sky-300/[.09] text-sky-100 hover:border-sky-100/45 hover:bg-sky-300/[.16] hover:shadow-[0_8px_22px_rgba(56,189,248,0.18)] focus-visible:ring-sky-200/70`}
      >
        {content}
      </a>
    )
  }
  if (token.kind === 'url' && token.href) {
    return (
      <a
        href={token.href}
        target="_blank"
        rel="noopener noreferrer"
        className={`${shared} ${interactive} border-violet-200/25 bg-violet-300/[.09] text-violet-100 hover:border-violet-100/45 hover:bg-violet-300/[.16] hover:shadow-[0_8px_22px_rgba(167,139,250,0.18)] focus-visible:ring-violet-200/70`}
      >
        <Link2 size={13} /> Link <ExternalLink size={11} />
      </a>
    )
  }
  return (
    <span className={`${shared} border-amber-200/25 bg-amber-300/[.09] text-amber-100 transition-shadow duration-200 ease-out hover:shadow-[0_0_16px_rgba(251,191,36,0.18)]`}>
      <Clock3 size={13} /> {token.value}
    </span>
  )
}

export function AnswerContent({ answer, citations }: { answer: string; citations: Citation[] }) {
  const evidence = new Map(
    citations.map((citation) => [citation.referenceId ?? shortEvidenceId(citation.evidenceId), citation]),
  )
  const jiraHref = buildJiraLinkResolver(citations)
  const tokens = tokenPattern(citations.filter((citation) => citation.source === 'jira' && safeExternalUrl(citation.url)).flatMap((citation) => citation.itemKey ? [citation.itemKey] : []))
  const pieces: React.ReactNode[] = []
  let cursor = 0

  for (const match of answer.matchAll(tokens)) {
    const value = match[0]
    const index = match.index ?? 0
    if (index > cursor) pieces.push(answer.slice(cursor, index))
    const token = tokenFor(value, evidence, jiraHref)
    pieces.push(token ? <Badge key={`${index}-${value}`} token={token} /> : value)
    cursor = index + value.length
  }
  if (cursor < answer.length) pieces.push(answer.slice(cursor))

  return <div className="whitespace-pre-wrap text-[15px] leading-8 text-white/80">{pieces}</div>
}
