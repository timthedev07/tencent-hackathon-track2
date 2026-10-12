'use client'

import { useState, useEffect, useCallback, Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import {
  CheckCircle2,
  Cloud,
  ExternalLink,
  FileText,
  Link2,
  Loader2,
  MessageCircle,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from 'lucide-react'
import { GlassPanel } from '@/components/GlassPanel'
import {
  ApiClientError,
  getAuthToken,
  getConnections,
  startOAuth,
  syncConnection,
  type ConnectionStatus,
} from '@/lib/api/client'

type SourceKey = 'confluence' | 'jira' | 'slack' | 'google_drive'

const SOURCE_LABELS: Record<SourceKey, string> = {
  confluence: 'Confluence',
  jira: 'Jira',
  slack: 'Slack',
  google_drive: 'Google Drive',
}

const PROVIDERS: {
  label: string
  key: 'atlassian' | 'slack' | 'google'
  sources: SourceKey[]
  description: string
  icon: typeof Link2
  tint: string
  dot: string
}[] = [
  {
    label: 'Atlassian',
    key: 'atlassian',
    sources: ['confluence', 'jira'],
    description: 'Confluence pages and Jira project knowledge',
    icon: Link2,
    tint: 'text-blue-200',
    dot: 'source-dot-confluence',
  },
  {
    label: 'Slack',
    key: 'slack',
    sources: ['slack'],
    description: 'Threads and conversations you can access',
    icon: MessageCircle,
    tint: 'text-pink-200',
    dot: 'source-dot-slack',
  },
  {
    label: 'Google Drive',
    key: 'google',
    sources: ['google_drive'],
    description: 'Documents and shared workspace files',
    icon: Cloud,
    tint: 'text-emerald-200',
    dot: 'source-dot-google',
  },
]

function SettingsContent() {
  const searchParams = useSearchParams()
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [connections, setConnections] = useState<Record<string, ConnectionStatus>>({})
  const [dataMode, setDataMode] = useState<'fixture' | 'live'>('fixture')
  const [loading, setLoading] = useState(true)
  const [connecting, setConnecting] = useState<string | null>(null)
  const [syncing, setSyncing] = useState<string | null>(null)

  const refreshConnections = useCallback(async () => {
    try {
      const data = await getConnections()
      setDataMode(data.dataMode)
      setConnections(
        Object.fromEntries(data.connections.map((connection) => [connection.source, connection])),
      )
    } catch (e) {
      if (e instanceof ApiClientError && e.status === 401)
        setError('Please sign in before connecting sources.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const refresh = window.setTimeout(() => {
      void refreshConnections()
    }, 0)
    return () => window.clearTimeout(refresh)
  }, [refreshConnections])
  useEffect(() => {
    const status = searchParams.get('status')
    const source = searchParams.get('source')
    const errorMsg = searchParams.get('error')
    if (!source) return
    const label =
      PROVIDERS.find(
        (provider) => provider.key === source || provider.sources.includes(source as SourceKey),
      )?.label ?? source
    const update = window.setTimeout(() => {
      if (status === 'connected') {
        setSuccess(`${label} connected successfully.`)
        void refreshConnections()
      }
      if (status === 'error')
        setError(`${label} connection failed${errorMsg ? `: ${decodeURIComponent(errorMsg)}` : ''}`)
    }, 0)
    return () => window.clearTimeout(update)
  }, [searchParams, refreshConnections])

  async function handleConnect(provider: 'atlassian' | 'slack' | 'google', label: string) {
    setError(null)
    setSuccess(null)
    setConnecting(label)
    try {
      const { authoriseUrl } = await startOAuth(provider)
      window.location.assign(authoriseUrl)
    } catch (e) {
      setError(e instanceof ApiClientError ? e.message : `Failed to start ${label} connection.`)
      setConnecting(null)
    }
  }

  async function handleSync(connectionId: string, label: string) {
    setError(null)
    setSuccess(null)
    setSyncing(connectionId)
    try {
      await syncConnection(connectionId)
      setSuccess(`${label} sync queued.`)
    } catch (e) {
      setError(e instanceof ApiClientError ? e.message : `Failed to sync ${label}.`)
    } finally {
      setSyncing(null)
    }
  }

  const hasToken = Boolean(getAuthToken())
  const connectedCount = PROVIDERS.filter((provider) =>
    provider.sources.some((source) => connections[source]?.connected),
  ).length

  if (loading) return <SettingsLoading />

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-10 flex flex-wrap items-end justify-between gap-5">
        <div>
          <div className="section-kicker flex items-center gap-2">
            <ShieldCheck size={13} /> Trust centre
          </div>
          <h1 className="page-title">Your connected sources.</h1>
          <p className="page-subtitle">
            Choose the places Relay can search. Connections are delegated to your identity and
            checked again at query time.
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-teal-200/10 bg-teal-300/[.06] px-3 py-2 text-xs text-teal-100">
          <span className="status-pulse" /> {connectedCount} of {PROVIDERS.length}{' '}
          {dataMode === 'fixture' ? 'demo sources available' : 'connected'}
        </div>
      </div>

      {dataMode === 'fixture' && (
        <GlassPanel className="mb-5 flex items-start gap-3 p-4" intensity="soft">
          <FileText size={17} className="mt-0.5 flex-none text-violet-200" />
          <div>
            <p className="text-xs font-semibold text-violet-100">Demo data mode</p>
            <p className="mt-1 text-xs leading-5 text-white/50">
              Relay is searching synthetic fixture data, not files from your provider accounts. Set{' '}
              <code>SOURCE_DATA_MODE=live</code> and restart the API to connect real sources.
            </p>
          </div>
        </GlassPanel>
      )}

      {!hasToken && (
        <GlassPanel className="mb-5 flex items-start gap-3 p-4" intensity="soft">
          <ShieldCheck size={17} className="mt-0.5 flex-none text-amber-200" />
          <div>
            <p className="text-xs font-semibold text-amber-100">Sign in to manage connections</p>
            <p className="mt-1 text-xs text-white/50">
              Relay needs your enterprise identity before it can link a source.
            </p>
            <a
              href="/login"
              className="mt-3 inline-flex text-xs font-medium text-violet-200 hover:text-violet-100"
            >
              Go to sign in <span className="ml-1">→</span>
            </a>
          </div>
        </GlassPanel>
      )}
      {success && <Notice icon={<CheckCircle2 size={16} />} tone="success" message={success} />}
      {error && <Notice icon={<XCircle size={16} />} tone="error" message={error} />}

      <div className="grid gap-4 lg:grid-cols-3">
        {PROVIDERS.map((provider) => {
          const providerConnections = provider.sources
            .map((source) => connections[source])
            .filter((connection): connection is ConnectionStatus => connection?.connected === true)
          const syncableConnections = providerConnections.filter(
            (
              connection,
            ): connection is ConnectionStatus & {
              connectionId: string
            } => connection.canSync && connection.connectionId !== null,
          )
          const connected = providerConnections.length > 0
          const displayNames = [
            ...new Set(
              providerConnections.map((connection) => connection.display_name ?? 'Identity linked'),
            ),
          ].join(', ')
          const Icon = provider.icon
          return (
            <GlassPanel
              key={provider.key}
              className="flex min-h-[260px] flex-col p-6"
              intensity={connected ? 'strong' : 'soft'}
              interactive
            >
              <div className="flex items-start justify-between">
                <div
                  className={`grid h-11 w-11 place-items-center rounded-lg border border-white/10 bg-white/[.05] ${provider.tint}`}
                >
                  <Icon size={20} />
                </div>
                <div className="flex flex-col items-end gap-2">
                  <span className="rounded-full border border-white/10 bg-white/[.04] px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-white/50">
                    {dataMode === 'fixture' ? 'Demo data' : 'Live OAuth'}
                  </span>
                  <span
                    className={`inline-flex items-center gap-1.5 text-xs font-medium ${connected ? 'text-teal-200' : 'text-white/35'}`}
                  >
                    <span className={`source-dot ${provider.dot}`} />{' '}
                    {connected
                      ? dataMode === 'fixture'
                        ? 'Demo available'
                        : 'Connected'
                      : 'Not connected'}
                  </span>
                </div>
              </div>
              <h2 className="mt-7 text-base font-semibold text-white">{provider.label}</h2>
              <p className="mt-2 text-xs leading-5 text-white/45">{provider.description}</p>
              {connected && (
                <p className="mt-3 truncate text-[11px] text-white/60">{displayNames}</p>
              )}
              <div className="mt-auto grid gap-2">
                {syncableConnections.map((connection) => {
                  const sourceLabel = SOURCE_LABELS[connection.source as SourceKey]
                  const syncLabel =
                    syncableConnections.length > 1 ? `Sync ${sourceLabel}` : 'Sync now'
                  return (
                    <button
                      key={connection.connectionId}
                      className="inline-flex h-10 items-center justify-center gap-2 rounded-lg text-xs font-semibold primary-glow disabled:opacity-40"
                      onClick={() =>
                        handleSync(
                          connection.connectionId,
                          syncableConnections.length > 1 ? sourceLabel : provider.label,
                        )
                      }
                      disabled={syncing === connection.connectionId}
                    >
                      {syncing === connection.connectionId ? (
                        <>
                          <Loader2 size={14} className="animate-spin" /> Queuing…
                        </>
                      ) : (
                        <>
                          <RefreshCw size={14} /> {syncLabel}
                        </>
                      )}
                    </button>
                  )
                })}
                <button
                  className="inline-flex h-10 items-center justify-center gap-2 rounded-lg text-xs font-medium ghost-glass disabled:opacity-40"
                  onClick={() => handleConnect(provider.key, provider.label)}
                  disabled={dataMode === 'fixture' || !hasToken || connecting === provider.label}
                  title={
                    dataMode === 'fixture'
                      ? 'Switch SOURCE_DATA_MODE to live before connecting a provider.'
                      : undefined
                  }
                >
                  {connecting === provider.label ? (
                    <>
                      <Loader2 size={14} className="animate-spin" /> Connecting…
                    </>
                  ) : (
                    <>
                      <ExternalLink size={14} />{' '}
                      {connected && dataMode === 'live'
                        ? `Reconnect ${provider.label}`
                        : `Connect ${provider.label}`}
                    </>
                  )}
                </button>
              </div>
            </GlassPanel>
          )
        })}
      </div>

      <GlassPanel className="mt-5 flex items-start gap-4 p-5" intensity="soft">
        <div className="grid h-9 w-9 flex-none place-items-center rounded-xl bg-violet-300/10 text-violet-200">
          <FileText size={16} />
        </div>
        <div>
          <h2 className="text-sm font-semibold text-white">How Relay keeps this safe</h2>
          <p className="mt-2 max-w-2xl text-xs leading-6 text-white/50">
            We use delegated access from your provider account. Search candidates are filtered
            locally, evidence is re-authorised live, and audit records are committed before an
            answer is shown.
          </p>
        </div>
      </GlassPanel>
    </div>
  )
}

function Notice({
  icon,
  tone,
  message,
}: {
  icon: React.ReactNode
  tone: 'success' | 'error'
  message: string
}) {
  return (
    <div
      className={`mb-5 flex items-center gap-3 rounded-xl border p-3 text-xs ${tone === 'success' ? 'border-teal-200/10 bg-teal-300/[.06] text-teal-100' : 'border-rose-200/10 bg-rose-300/[.06] text-rose-100'}`}
    >
      {icon}
      <span>{message}</span>
    </div>
  )
}
function SettingsLoading() {
  return (
    <div className="mx-auto max-w-5xl">
      <div className="h-3 w-24 animate-pulse rounded bg-white/10" />
      <div className="mt-4 h-12 w-96 max-w-full animate-pulse rounded bg-white/10" />
      <div className="mt-10 grid gap-4 md:grid-cols-3">
        {[1, 2, 3].map((item) => (
          <div
            key={item}
            className="h-64 animate-pulse rounded-3xl border border-white/5 bg-white/[.03]"
          />
        ))}
      </div>
      <div className="mt-8 flex items-center gap-2 text-xs text-white/35">
        <RefreshCw size={14} className="animate-spin" /> Checking connections…
      </div>
    </div>
  )
}

export default function SettingsPage() {
  return (
    <Suspense fallback={<SettingsLoading />}>
      <SettingsContent />
    </Suspense>
  )
}
