'use client'

import { use, useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, FileSearch, Fingerprint, ShieldAlert, ShieldCheck } from 'lucide-react'
import { GlassPanel } from '@/components/GlassPanel'
import { ApiClientError, getAuditCapsule, type AuditResponse } from '@/lib/api/client'

export default function AuditPage({ params }: { params: Promise<{ queryId: string }> }) {
  const { queryId } = use(params)
  const [audit, setAudit] = useState<AuditResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  useEffect(() => {
    getAuditCapsule(queryId)
      .then(setAudit)
      .catch((e) =>
        setError(e instanceof ApiClientError ? e.message : 'Failed to load the audit record.'),
      )
      .finally(() => setIsLoading(false))
  }, [queryId])

  if (isLoading) return <AuditLoading />
  if (error || !audit)
    return (
      <div className="mx-auto max-w-3xl">
        <GlassPanel className="p-8 text-center" intensity="soft">
          <ShieldAlert size={26} className="mx-auto text-rose-200" />
          <h1 className="mt-4 text-lg font-semibold text-white">Audit record unavailable</h1>
          <p className="mx-auto mt-2 max-w-md text-xs leading-5 text-white/45">
            {error ?? 'You may not have the compliance role, or this query does not exist.'}
          </p>
          <Link
            href="/query"
            className="mt-6 inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs ghost-glass"
          >
            <ArrowLeft size={13} /> Back to query
          </Link>
        </GlassPanel>
      </div>
    )

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <Link
            href="/query"
            className="mb-5 inline-flex items-center gap-2 text-xs text-white/45 hover:text-white"
          >
            <ArrowLeft size={13} /> Back to query
          </Link>
          <div className="section-kicker flex items-center gap-2">
            <FileSearch size={13} /> Compliance record
          </div>
          <h1 className="page-title text-4xl">Audit trail.</h1>
          <p className="page-subtitle">A read-only evidence capsule for this exact query.</p>
        </div>
        <span
          className={`inline-flex items-center gap-2 rounded-full border px-3 py-2 text-xs ${audit.auditChainVerified ? 'border-teal-200/10 bg-teal-300/[.06] text-teal-100' : 'border-rose-200/10 bg-rose-300/[.06] text-rose-100'}`}
        >
          {audit.auditChainVerified ? <ShieldCheck size={14} /> : <ShieldAlert size={14} />}{' '}
          {audit.auditChainVerified ? 'Chain verified' : 'Verification failed'}
        </span>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <InfoCard
          title="Query details"
          icon={<FileSearch size={15} />}
          rows={[
            ['Query ID', audit.queryId],
            ['Status', audit.queryStatus ?? '—'],
            ['Timestamp', formatTimestamp(audit.timestamp)],
            ['User', audit.userIdentifier ?? '—'],
          ]}
        />
        <InfoCard
          title="Model and prompt"
          icon={<Fingerprint size={15} />}
          rows={[
            ['Model version', audit.modelVersion ?? '—'],
            ['Prompt version', audit.promptVersion ?? '—'],
            ['Evidence items', String(audit.evidenceManifest.length)],
            ['Citations', String(audit.citations.length)],
          ]}
        />
      </div>
      <AuditTable
        title="Access decisions"
        copy="Source-level authorisation decisions recorded during the query."
        headers={['Source', 'Decision', 'Reason', 'Checked at']}
        rows={audit.accessDecisions.map((decision) => [
          decision.source,
          decision.decision,
          decision.reasonCode ?? '—',
          formatTimestamp(decision.accessCheckedAt),
        ])}
      />
      <AuditTable
        title="Evidence manifest"
        copy="Immutable source revisions included in the authorised context."
        headers={['Source', 'Rank', 'Source revision']}
        rows={audit.evidenceManifest.map((evidence) => [
          evidence.source,
          String(evidence.rank),
          evidence.sourceRevision,
        ])}
      />
      {(audit.partialSources.length > 0 || audit.errors.length > 0) && (
        <GlassPanel className="mt-4 p-5" intensity="soft">
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            <ShieldAlert size={15} className="text-amber-200" /> Partial sources and errors
          </div>
          <p className="mt-2 text-xs leading-5 text-white/45">
            Some provider checks or pipeline events were recorded as incomplete. No unauthorised
            text is exposed here.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <span className="source-chip">{audit.partialSources.length} partial source events</span>
            <span className="source-chip">{audit.errors.length} recorded errors</span>
          </div>
        </GlassPanel>
      )}
    </div>
  )
}

function InfoCard({
  title,
  icon,
  rows,
}: {
  title: string
  icon: React.ReactNode
  rows: string[][]
}) {
  return (
    <GlassPanel className="p-5" intensity="soft">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-white">
        <span className="text-violet-200">{icon}</span>
        {title}
      </div>
      <div className="space-y-3">
        {rows.map(([label, value]) => (
          <div
            key={label}
            className="flex items-start justify-between gap-4 border-b border-white/6 pb-2 text-xs last:border-0 last:pb-0"
          >
            <span className="text-white/40">{label}</span>
            <span className="max-w-[70%] break-all text-right font-mono text-[11px] text-white/70">
              {value}
            </span>
          </div>
        ))}
      </div>
    </GlassPanel>
  )
}
function AuditTable({
  title,
  copy,
  headers,
  rows,
}: {
  title: string
  copy: string
  headers: string[]
  rows: string[][]
}) {
  return (
    <GlassPanel className="mt-4 overflow-hidden" intensity="soft">
      <div className="border-b border-white/7 p-5">
        <h2 className="text-sm font-semibold text-white">{title}</h2>
        <p className="mt-1 text-xs text-white/40">{copy}</p>
      </div>
      {rows.length === 0 ? (
        <p className="p-5 text-xs text-white/40">No records were captured.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px] text-left text-xs">
            <thead>
              <tr className="border-b border-white/7 text-white/35">
                {headers.map((header) => (
                  <th key={header} className="px-5 py-3 font-medium">
                    {header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row[0]}-${index}`} className="border-b border-white/5 last:border-0">
                  {row.map((cell, cellIndex) => (
                    <td
                      key={`${cell}-${cellIndex}`}
                      className={`px-5 py-3 ${cellIndex === 1 && (cell === 'allow' || cell === 'deny') ? (cell === 'allow' ? 'text-teal-200' : 'text-rose-200') : 'text-white/60'}`}
                    >
                      {cellIndex === 1 && (cell === 'allow' || cell === 'deny') ? (
                        <span className="inline-flex items-center gap-1.5">
                          <span className="h-1.5 w-1.5 rounded-full bg-current" />
                          {cell}
                        </span>
                      ) : (
                        cell
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </GlassPanel>
  )
}
function formatTimestamp(timestamp: string | null) {
  if (!timestamp) return '—'
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime())
    ? timestamp
    : new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}
function AuditLoading() {
  return (
    <div className="mx-auto max-w-5xl">
      <div className="h-3 w-28 animate-pulse rounded bg-white/10" />
      <div className="mt-5 h-12 w-64 animate-pulse rounded bg-white/10" />
      <div className="mt-9 grid gap-4 md:grid-cols-2">
        <div className="h-44 animate-pulse rounded-3xl bg-white/[.03]" />
        <div className="h-44 animate-pulse rounded-3xl bg-white/[.03]" />
      </div>
      <div className="mt-4 h-60 animate-pulse rounded-3xl bg-white/[.03]" />
    </div>
  )
}
