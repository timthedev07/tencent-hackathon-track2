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
  type ConnectionStatus,
} from '@/lib/api/client'

const PROVIDERS = [
  {
    label: 'Atlassian',
    key: 'atlassian' as const,
    source: 'confluence',
    description: 'Confluence pages and Jira project knowledge',
    icon: Link2,
    tint: 'text-blue-200',
    dot: 'source-dot-confluence',
  },
  {
    label: 'Slack',
    key: 'slack' as const,
    source: 'slack',
    description: 'Threads and conversations you can access',
    icon: MessageCircle,
    tint: 'text-pink-200',
    dot: 'source-dot-slack',
  },
  {
    label: 'Google Drive',
    key: 'google' as const,
    source: 'google_drive',
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
  const [loading, setLoading] = useState(true)
  const [connecting, setConnecting] = useState<string | null>(null)

  const refreshConnections = useCallback(async () => {
    try {
      const data = await getConnections()
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
    const label = PROVIDERS.find((provider) => provider.source === source)?.label ?? source
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

  const hasToken = Boolean(getAuthToken())
  const connectedCount = Object.values(connections).filter(
    (connection) => connection.connected,
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
          <span className="status-pulse" /> {connectedCount} of {PROVIDERS.length} connected
        </div>
      </div>

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
          const connection = connections[provider.source]
          const connected = connection?.connected ?? false
          const Icon = provider.icon
          return (
            <GlassPanel
              key={provider.source}
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
                <span
                  className={`inline-flex items-center gap-1.5 text-xs font-medium ${connected ? 'text-teal-200' : 'text-white/35'}`}
                >
                  <span className={`source-dot ${provider.dot}`} />{' '}
                  {connected ? 'Connected' : 'Not connected'}
                </span>
              </div>
              <h2 className="mt-7 text-base font-semibold text-white">{provider.label}</h2>
              <p className="mt-2 text-xs leading-5 text-white/45">{provider.description}</p>
              {connected && (
                <p className="mt-3 truncate text-[11px] text-white/60">
                  {connection.display_name ?? 'Identity linked'}
                </p>
              )}
              <button
                className="mt-auto inline-flex h-10 items-center justify-center gap-2 rounded-lg text-xs font-medium ghost-glass disabled:opacity-40"
                onClick={() => handleConnect(provider.key, provider.label)}
                disabled={!hasToken || connecting === provider.label}
              >
                {connecting === provider.label ? (
                  <>
                    <Loader2 size={14} className="animate-spin" /> Connecting…
                  </>
                ) : (
                  <>
                    <ExternalLink size={14} />{' '}
                    {connected ? `Reconnect ${provider.label}` : `Connect ${provider.label}`}
                  </>
                )}
              </button>
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
