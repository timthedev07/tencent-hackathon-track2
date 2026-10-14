'use client'

import { useState, useEffect, useCallback, useRef, Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import {
  CheckCircle2,
  Cloud,
  ExternalLink,
  FileText,
  FolderOpen,
  Link2,
  Loader2,
  MessageCircle,
  Plus,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Trash2,
  X,
  XCircle,
} from 'lucide-react'
import { GlassPanel } from '@/components/GlassPanel'
import {
  ApiClientError,
  getAuthToken,
  getBackfillPreviews,
  getBackfillStatus,
  getConnections,
  getGoogleDriveFolderMetadata,
  restartConnection,
  startOAuth,
  syncConnection,
  triggerManualBackfill,
  updateGoogleDriveScope,
  type BackfillPreviewItem,
  type BackfillPreviewResponse,
  type BackfillRunStatus,
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

function isGoogleDriveFolderLink(value: string): boolean {
  try {
    const url = new URL(value)
    const isDriveHost = ['drive.google.com', 'www.drive.google.com'].includes(url.hostname)
    return (
      url.protocol === 'https:' &&
      isDriveHost &&
      (url.pathname.includes('/folders/') || url.searchParams.has('id'))
    )
  } catch {
    return false
  }
}

function SettingsContent() {
  const searchParams = useSearchParams()
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [connections, setConnections] = useState<Record<string, ConnectionStatus>>({})
  const [loading, setLoading] = useState(true)
  const [connecting, setConnecting] = useState<string | null>(null)
  const [syncing, setSyncing] = useState<string | null>(null)
  const [restarting, setRestarting] = useState<string | null>(null)
  const [syncRuns, setSyncRuns] = useState<Record<string, BackfillRunStatus>>({})
  const [previews, setPreviews] = useState<BackfillPreviewResponse | null>(null)
  const [refreshingPreviews, setRefreshingPreviews] = useState(false)
  const [driveRootFolderLinks, setDriveRootFolderLinks] = useState('')
  const [savingDriveScope, setSavingDriveScope] = useState(false)
  const [driveScopeModalOpen, setDriveScopeModalOpen] = useState(false)
  const [driveScopeDraftLinks, setDriveScopeDraftLinks] = useState<string[]>([])
  const [driveFolderNames, setDriveFolderNames] = useState<Record<string, string>>({})
  const [loadingDriveFolderNames, setLoadingDriveFolderNames] = useState(false)
  const [driveScopeInput, setDriveScopeInput] = useState('')
  const [driveScopeInputError, setDriveScopeInputError] = useState<string | null>(null)
  const activeRunIds = useRef(new Set<string>())
  const driveScopeInputRef = useRef<HTMLInputElement>(null)
  const driveScopeTriggerRef = useRef<HTMLButtonElement>(null)

  const refreshConnections = useCallback(async () => {
    try {
      const data = await getConnections()
      setConnections(
        Object.fromEntries(data.connections.map((connection) => [connection.source, connection])),
      )
      const drive = data.connections.find((connection) => connection.source === 'google_drive')
      if (drive) setDriveRootFolderLinks(drive.rootFolderLinks.join('\n'))
    } catch (e) {
      if (e instanceof ApiClientError && e.status === 401)
        setError('Please sign in before connecting sources.')
    } finally {
      setLoading(false)
    }
  }, [])

  const refreshPreviews = useCallback(async () => {
    setRefreshingPreviews(true)
    try {
      setPreviews(await getBackfillPreviews())
    } catch (e) {
      if (e instanceof ApiClientError && e.status === 401)
        setError('Please sign in before viewing imported items.')
    } finally {
      setRefreshingPreviews(false)
    }
  }, [])

  const refreshBackfillStatus = useCallback(async () => {
    try {
      const data = await getBackfillStatus()
      const completedNow = data.runs.some(
        (run) => activeRunIds.current.has(run.id) && !isActiveRun(run),
      )
      const failedNow = data.runs.find(
        (run) => activeRunIds.current.has(run.id) && run.status === 'failed',
      )
      activeRunIds.current = new Set(data.runs.filter(isActiveRun).map((run) => run.id))
      setSyncRuns(Object.fromEntries(data.runs.map((run) => [run.source, run])))
      if (failedNow) {
        setError(failedNow.errorMessage ?? 'The import worker did not start. Please retry.')
      } else if (completedNow) {
        void refreshPreviews()
        setSuccess('Import complete. The latest authorised items are shown below.')
      }
    } catch (e) {
      if (e instanceof ApiClientError && e.status === 401)
        setError('Please sign in to view import progress.')
    }
  }, [refreshPreviews])

  useEffect(() => {
    const refresh = window.setTimeout(() => {
      void refreshConnections()
      void refreshPreviews()
      void refreshBackfillStatus()
    }, 0)
    return () => window.clearTimeout(refresh)
  }, [refreshBackfillStatus, refreshConnections, refreshPreviews])
  useEffect(() => {
    if (!Object.values(syncRuns).some(isActiveRun)) return
    const poll = window.setInterval(() => {
      void refreshBackfillStatus()
    }, 2000)
    return () => window.clearInterval(poll)
  }, [refreshBackfillStatus, syncRuns])
  useEffect(() => {
    if (!driveScopeModalOpen) return
    const previousFocus = document.activeElement as HTMLElement | null
    const previousOverflow = document.body.style.overflow
    const focusTimer = window.setTimeout(() => driveScopeInputRef.current?.focus(), 0)
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !savingDriveScope) setDriveScopeModalOpen(false)
    }
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      window.clearTimeout(focusTimer)
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', handleKeyDown)
      window.setTimeout(() => previousFocus?.focus(), 0)
    }
  }, [driveScopeModalOpen, savingDriveScope])
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

  async function handleSync(
    provider: 'atlassian' | 'slack' | 'google_drive',
    label: string,
    source: string,
  ) {
    setError(null)
    setSuccess(null)
    setSyncing(source)
    try {
      const connection = connections[source]
      if (connection?.connectionId) {
        await syncConnection(connection.connectionId)
        setSuccess(`${label}: sync queued.`)
      } else {
        const result = await triggerManualBackfill(provider)
        activeRunIds.current = new Set(result.runs.map((run) => run.id))
        setSyncRuns((current) => ({
          ...current,
          ...Object.fromEntries(result.runs.map((run) => [run.source, run])),
        }))
        setSuccess(`${label}: ${result.message}`)
      }
      void refreshBackfillStatus()
    } catch (e) {
      setError(e instanceof ApiClientError ? e.message : `Failed to queue the ${label} import.`)
    } finally {
      setSyncing(null)
    }
  }

  async function handleRestart(connectionId: string, label: string, source: string) {
    setError(null)
    setSuccess(null)
    setRestarting(source)
    try {
      await restartConnection(connectionId)
      setSuccess(`${label}: restart queued.`)
      void refreshBackfillStatus()
    } catch (e) {
      setError(e instanceof ApiClientError ? e.message : `Failed to restart the ${label} import.`)
    } finally {
      setRestarting(null)
    }
  }

  async function resolveDriveFolderNames(connectionId: string, links: string[]) {
    if (links.length === 0) return
    setLoadingDriveFolderNames(true)
    try {
      const response = await getGoogleDriveFolderMetadata(connectionId, links)
      const resolvedNames = Object.fromEntries(
        response.folders
          .filter((folder): folder is typeof folder & { name: string } => Boolean(folder.name))
          .map((folder) => [folder.link, folder.name]),
      )
      setDriveFolderNames((current) => ({ ...current, ...resolvedNames }))
    } catch {
      // Folder links remain editable when Drive metadata cannot be loaded.
    } finally {
      setLoadingDriveFolderNames(false)
    }
  }

  function openDriveScopeModal(connection: ConnectionStatus & { connectionId: string }) {
    setDriveScopeDraftLinks(connection.rootFolderLinks)
    setDriveFolderNames({})
    setDriveScopeInput('')
    setDriveScopeInputError(null)
    setDriveScopeModalOpen(true)
    void resolveDriveFolderNames(connection.connectionId, connection.rootFolderLinks)
  }

  function addDriveScopeLink(connectionId: string | null) {
    const candidates = driveScopeInput
      .split(/[\n,]+/)
      .map((value) => value.trim())
      .filter(Boolean)
    if (candidates.length === 0) return
    const invalidLink = candidates.find((value) => !isGoogleDriveFolderLink(value))
    if (invalidLink) {
      setDriveScopeInputError('Use an https://drive.google.com folder link.')
      return
    }
    setDriveScopeDraftLinks((current) => [...new Set([...current, ...candidates])])
    setDriveScopeInput('')
    setDriveScopeInputError(null)
    if (connectionId) void resolveDriveFolderNames(connectionId, candidates)
  }

  async function handleSaveDriveScope(connectionId: string, links?: string[]) {
    const rootFolderLinks = [
      ...new Set(
        (links ?? driveRootFolderLinks.split(/[\n,]+/))
          .map((value) => value.trim())
          .filter(Boolean),
      ),
    ]
    setError(null)
    setSuccess(null)
    if (rootFolderLinks.length === 0) {
      setError('Paste at least one Google Drive folder link before saving the scope.')
      return false
    }
    setSavingDriveScope(true)
    try {
      await updateGoogleDriveScope(connectionId, rootFolderLinks)
      void refreshConnections()
      try {
        await restartConnection(connectionId)
        setSuccess('Google Drive scope saved. A scoped import has been queued.')
        void refreshBackfillStatus()
      } catch (e) {
        const reason =
          e instanceof ApiClientError
            ? e.message
            : e instanceof Error
              ? e.message
              : 'The sync could not be queued.'
        setSuccess('Google Drive scope saved, but the import could not be queued.')
        setError(
          `Scope saved. ${reason} Reconnect Google Drive, then sync again if authorisation has expired.`,
        )
      }
    } catch (e) {
      const reason =
        e instanceof ApiClientError
          ? e.message
          : e instanceof Error
            ? e.message
            : 'The API request failed.'
      setError(`Failed to save the Google Drive scope: ${reason}`)
      return false
    } finally {
      setSavingDriveScope(false)
    }
    return true
  }

  async function handleSaveDriveScopeModal(connectionId: string) {
    const saved = await handleSaveDriveScope(connectionId, driveScopeDraftLinks)
    if (saved) {
      setDriveRootFolderLinks(driveScopeDraftLinks.join('\n'))
      setDriveScopeModalOpen(false)
    }
  }

  const hasToken = Boolean(getAuthToken())
  const connectedCount = PROVIDERS.filter((provider) =>
    provider.sources.some((source) => connections[source]?.connected),
  ).length
  const googleConnection = connections.google_drive
  const googleConnectionId =
    googleConnection?.connected && googleConnection.connectionId
      ? googleConnection.connectionId
      : null
  const driveScopeLinks = driveRootFolderLinks
    .split(/[\n,]+/)
    .map((value) => value.trim())
    .filter(Boolean)

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
        <div className="flex items-center gap-2">
          <button
            className="inline-flex h-9 items-center gap-2 rounded-lg px-3 text-xs font-medium ghost-glass disabled:opacity-40"
            onClick={() => void refreshPreviews()}
            disabled={!hasToken || refreshingPreviews}
          >
            {refreshingPreviews ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <RefreshCw size={13} />
            )}{' '}
            Refresh imported items
          </button>
          <div className="flex items-center gap-2 rounded-full border border-teal-200/10 bg-teal-300/[.06] px-3 py-2 text-xs text-teal-100">
            <span className="status-pulse" /> {connectedCount} of {PROVIDERS.length} connected
          </div>
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
      <div className="source-grid">
        {PROVIDERS.map((provider) => {
          const providerConnections = provider.sources
            .map((source) => connections[source])
            .filter((connection): connection is ConnectionStatus => connection?.connected === true)
          const syncableConnections = providerConnections.filter(
            (connection): connection is ConnectionStatus & { connectionId: string } =>
              connection.connectionId !== null,
          )
          const syncProvider = provider.key === 'google' ? 'google_drive' : provider.key
          const connected = providerConnections.length > 0
          const displayNames = [
            ...new Set(
              providerConnections.map((connection) => connection.display_name ?? 'Identity linked'),
            ),
          ].join(', ')
          const Icon = provider.icon
          const syncRun = providerRun(provider.key, syncRuns)
          return (
            <GlassPanel
              key={provider.key}
              className="source-card"
              intensity={connected ? 'strong' : 'soft'}
              interactive
            >
              <div className="source-card-head">
                <div className="source-brand">
                  <div className={`source-icon ${provider.tint}`}>
                    <Icon size={19} />
                  </div>
                  <div className="min-w-0">
                    <h2 className="source-card-title">{provider.label}</h2>
                    <p className="source-card-type">
                      {provider.sources.map((source) => SOURCE_LABELS[source]).join(' + ')}
                    </p>
                  </div>
                </div>
                <span
                  className={`source-status ${connected ? 'source-status-connected' : 'source-status-muted'}`}
                >
                  <span className={`source-dot ${provider.dot}`} />{' '}
                  {connected ? 'Connected' : 'Not connected'}
                </span>
              </div>

              <div className="source-card-body">
                <p className="source-description">{provider.description}</p>
                {connected ? (
                  <div className="source-account" title={displayNames}>
                    <span>Signed in as</span>
                    <strong>{displayNames}</strong>
                  </div>
                ) : (
                  <div className="source-empty-state">
                    <p>No account linked yet</p>
                    <span>Connect this source to make its content searchable.</span>
                  </div>
                )}
                {provider.key === 'google' && connected && syncableConnections[0] && (
                  <div className="scope-summary mt-4">
                    <div className="scope-summary-head">
                      <div className="scope-summary-icon">
                        <FolderOpen size={15} />
                      </div>
                      <div className="min-w-0">
                        <p className="scope-summary-label">Search boundary</p>
                        <p className="scope-summary-value">
                          {driveScopeLinks.length > 0
                            ? `${driveScopeLinks.length} folder${driveScopeLinks.length === 1 ? '' : 's'} selected`
                            : 'No folders selected'}
                        </p>
                      </div>
                    </div>
                    <p className="scope-summary-note">
                      Relay searches these folders and everything nested inside them.
                    </p>
                    <button
                      ref={driveScopeTriggerRef}
                      className="scope-manage-button"
                      onClick={() => void openDriveScopeModal(syncableConnections[0])}
                      disabled={!hasToken || savingDriveScope}
                    >
                      <Settings2 size={14} /> Manage folders
                    </button>
                  </div>
                )}
                {connected && <SyncProgress run={syncRun} />}
              </div>

              <div className="source-card-footer">
                <div
                  className={`source-primary-actions ${syncableConnections.length > 1 ? 'source-primary-actions-split' : ''}`}
                >
                  {syncableConnections.map((connection) => {
                    const sourceLabel = SOURCE_LABELS[connection.source as SourceKey]
                    const label =
                      syncableConnections.length > 1
                        ? `Sync ${sourceLabel}`
                        : syncRun?.status === 'completed'
                          ? 'Sync again'
                          : 'Sync now'
                    const sourceRun = syncRuns[connection.source]
                    const sourceActive = sourceRun ? isActiveRun(sourceRun) : false
                    return (
                      <button
                        key={connection.connectionId}
                        className="source-action source-action-primary"
                        onClick={() =>
                          sourceActive
                            ? handleRestart(connection.connectionId, sourceLabel, connection.source)
                            : handleSync(syncProvider, sourceLabel, connection.source)
                        }
                        disabled={
                          !hasToken ||
                          syncing === connection.source ||
                          restarting === connection.source
                        }
                      >
                        {syncing === connection.source ? (
                          <>
                            <Loader2 size={14} className="animate-spin" /> Queueing…
                          </>
                        ) : restarting === connection.source ? (
                          <>
                            <Loader2 size={14} className="animate-spin" /> Restarting…
                          </>
                        ) : sourceActive ? (
                          <>
                            <RefreshCw size={14} /> Restart import
                          </>
                        ) : (
                          <>
                            <RefreshCw size={14} /> {label}
                          </>
                        )}
                      </button>
                    )
                  })}
                </div>
                <button
                  className="source-action source-action-secondary"
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
              </div>
            </GlassPanel>
          )
        })}
      </div>

      {driveScopeModalOpen && googleConnectionId && (
        <div
          className="scope-modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !savingDriveScope)
              setDriveScopeModalOpen(false)
          }}
        >
          <section
            className="scope-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="scope-modal-title"
            aria-describedby="scope-modal-description"
          >
            <div className="scope-modal-header">
              <div className="scope-modal-heading">
                <div className="scope-modal-icon">
                  <FolderOpen size={18} />
                </div>
                <div>
                  <p className="section-kicker text-[10px] font-semibold uppercase tracking-[0.14em]">
                    Google Drive access
                  </p>
                  <h2 id="scope-modal-title" className="scope-modal-title">
                    Choose searchable folders
                  </h2>
                </div>
              </div>
              <button
                className="scope-modal-close"
                onClick={() => setDriveScopeModalOpen(false)}
                disabled={savingDriveScope}
                aria-label="Close folder access dialog"
              >
                <X size={17} />
              </button>
            </div>

            <div className="scope-modal-body">
              <p id="scope-modal-description" className="scope-modal-description">
                Add the Drive folders Relay is allowed to index. Nested folders are included
                automatically, so one carefully chosen root is usually enough.
              </p>

              <form
                className="scope-add-form"
                onSubmit={(event) => {
                  event.preventDefault()
                  addDriveScopeLink(googleConnectionId)
                }}
              >
                <label htmlFor="drive-folder-link" className="scope-field-label">
                  Folder link
                </label>
                <div className="scope-add-row">
                  <div className="scope-input-wrap">
                    <Link2 size={15} aria-hidden="true" />
                    <input
                      ref={driveScopeInputRef}
                      id="drive-folder-link"
                      className="scope-link-input"
                      value={driveScopeInput}
                      onChange={(event) => {
                        setDriveScopeInput(event.target.value)
                        setDriveScopeInputError(null)
                      }}
                      placeholder="https://drive.google.com/drive/folders/..."
                      autoComplete="off"
                      aria-invalid={Boolean(driveScopeInputError)}
                      aria-describedby={driveScopeInputError ? 'scope-link-error' : undefined}
                    />
                  </div>
                  <button type="submit" className="scope-add-button">
                    <Plus size={15} /> Add folder
                  </button>
                </div>
                {driveScopeInputError && (
                  <p id="scope-link-error" className="scope-field-error" role="alert">
                    {driveScopeInputError}
                  </p>
                )}
              </form>

              <div className="scope-list-heading">
                <div>
                  <p className="scope-field-label">Allowed folders</p>
                  <p className="scope-list-count" aria-live="polite">
                    {driveScopeDraftLinks.length === 0
                      ? 'Nothing selected yet'
                      : `${driveScopeDraftLinks.length} folder${driveScopeDraftLinks.length === 1 ? '' : 's'} will be indexed`}
                  </p>
                </div>
                <span className="scope-list-badge">Nested folders included</span>
              </div>

              {driveScopeDraftLinks.length > 0 ? (
                <ul className="scope-folder-list" aria-label="Allowed Google Drive folders">
                  {driveScopeDraftLinks.map((link, index) => (
                    <li key={link} className="scope-folder-row">
                      <span className="scope-folder-number" aria-hidden="true">
                        {String(index + 1).padStart(2, '0')}
                      </span>
                      <span className="scope-folder-main">
                        <strong>
                          {driveFolderNames[link] ??
                            (loadingDriveFolderNames
                              ? 'Loading folder name…'
                              : 'Folder name unavailable')}
                        </strong>
                        <span title={link}>{link}</span>
                      </span>
                      <a
                        className="scope-folder-open"
                        href={link}
                        target="_blank"
                        rel="noreferrer"
                        aria-label={`Open Drive folder ${index + 1} in a new tab`}
                      >
                        <ExternalLink size={14} />
                      </a>
                      <button
                        className="scope-folder-remove"
                        onClick={() =>
                          setDriveScopeDraftLinks((current) =>
                            current.filter((item) => item !== link),
                          )
                        }
                        disabled={savingDriveScope}
                        aria-label={`Remove Drive folder ${index + 1}`}
                      >
                        <Trash2 size={14} />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="scope-list-empty">
                  <FolderOpen size={20} />
                  <div>
                    <strong>Add your first folder</strong>
                    <span>Only content inside selected folders will be searchable.</span>
                  </div>
                </div>
              )}

              <div className="scope-modal-note">
                <ShieldCheck size={15} />
                <span>Changing this list makes the next sync the source of truth for Drive.</span>
              </div>
            </div>

            <div className="scope-modal-footer">
              <button
                className="scope-cancel-button"
                onClick={() => setDriveScopeModalOpen(false)}
                disabled={savingDriveScope}
              >
                Cancel
              </button>
              <button
                className="scope-save-button"
                onClick={() => void handleSaveDriveScopeModal(googleConnectionId)}
                disabled={!hasToken || savingDriveScope || driveScopeDraftLinks.length === 0}
              >
                {savingDriveScope ? (
                  <>
                    <Loader2 size={15} className="animate-spin" /> Saving and syncing…
                  </>
                ) : (
                  <>
                    <CheckCircle2 size={15} /> Save scope and sync
                  </>
                )}
              </button>
            </div>
          </section>
        </div>
      )}

      <ImportedContent previews={previews} />

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
function isActiveRun(run: BackfillRunStatus): boolean {
  return run.status === 'queued' || run.status === 'scanning' || run.status === 'processing'
}
function providerRun(
  provider: 'atlassian' | 'slack' | 'google',
  runs: Record<string, BackfillRunStatus>,
): BackfillRunStatus | undefined {
  const sources =
    provider === 'atlassian'
      ? ['confluence', 'jira']
      : [provider === 'google' ? 'google_drive' : 'slack']
  const sourceRuns = sources
    .map((source) => runs[source])
    .filter((run): run is BackfillRunStatus => Boolean(run))
  if (sourceRuns.length === 0) return undefined
  const active = sourceRuns.find(isActiveRun)
  const failed = sourceRuns.every((run) => run.status === 'failed')
  const hasErrors = sourceRuns.some(
    (run) => run.failedCount > 0 || run.status === 'completed_with_errors',
  )
  const latest = sourceRuns[0]
  return {
    ...latest,
    status:
      active?.status ?? (failed ? 'failed' : hasErrors ? 'completed_with_errors' : 'completed'),
    discoveredCount: sourceRuns.reduce((total, run) => total + run.discoveredCount, 0),
    enqueuedCount: sourceRuns.reduce((total, run) => total + run.enqueuedCount, 0),
    processedCount: sourceRuns.reduce((total, run) => total + run.processedCount, 0),
    failedCount: sourceRuns.reduce((total, run) => total + run.failedCount, 0),
    errorMessage: sourceRuns.find((run) => run.errorMessage)?.errorMessage ?? null,
  }
}
function SyncProgress({ run }: { run: BackfillRunStatus | undefined }) {
  if (!run) return null
  if (run.status === 'failed')
    return (
      <div className="sync-status sync-status-error">
        <div className="sync-status-title">
          <XCircle size={14} />
          <strong>Import failed</strong>
        </div>
        <p>{run.errorMessage ?? 'The import worker did not start. Try syncing again.'}</p>
      </div>
    )
  if (isActiveRun(run)) {
    const label =
      run.status === 'queued'
        ? 'Waiting for worker…'
        : run.status === 'scanning'
          ? 'Scanning source…'
          : `Indexed ${run.processedCount} of ${run.enqueuedCount} items`
    const progress =
      run.enqueuedCount > 0
        ? Math.min(100, Math.round((run.processedCount / run.enqueuedCount) * 100))
        : 0
    return (
      <div className="sync-status sync-status-active">
        <div className="sync-status-head">
          <div className="sync-status-title">
            <Loader2 size={14} className="animate-spin" />
            <strong>{label}</strong>
          </div>
          {run.enqueuedCount > 0 && <strong className="sync-progress-percent">{progress}%</strong>}
        </div>
        <div className="sync-progress-track">
          <div
            className={`sync-progress-bar ${run.enqueuedCount === 0 ? 'sync-progress-indeterminate' : ''}`}
            style={run.enqueuedCount > 0 ? { width: `${progress}%` } : undefined}
          />
        </div>
        <p className="sync-progress-note">
          {run.enqueuedCount > 0
            ? `${run.processedCount} of ${run.enqueuedCount} items indexed`
            : 'Preparing the import…'}
        </p>
      </div>
    )
  }
  if (run.status === 'completed_with_errors')
    return (
      <div className="sync-status sync-status-warning">
        <div className="sync-status-title">
          <XCircle size={14} />
          <strong>Import completed with issues</strong>
        </div>
        <p>
          {run.processedCount} of {run.enqueuedCount} items indexed, {run.failedCount} failed.
          {run.errorMessage
            ? ` Failure detail: ${run.errorMessage}`
            : ' Retry the import to recover them.'}
        </p>
      </div>
    )
  return (
    <div className="sync-status sync-status-complete">
      <CheckCircle2 size={14} />
      <span>
        Last import: {run.processedCount} of {run.enqueuedCount} items indexed.
      </span>
    </div>
  )
}
function AtlassianPreview({
  preview,
}: {
  preview: BackfillPreviewResponse['atlassian'] | undefined
}) {
  if (!preview) return <PreviewEmpty />
  const hasItems = preview.pages.length > 0 || preview.tickets.length > 0
  if (!hasItems) return <PreviewEmpty />
  return (
    <div className="atlassian-preview">
      <PreviewList heading="Confluence pages" items={preview.pages} />
      <PreviewList heading="Jira tickets" items={preview.tickets} />
    </div>
  )
}
function ImportedContent({ previews }: { previews: BackfillPreviewResponse | null }) {
  return (
    <GlassPanel className="indexed-content-panel" intensity="soft">
      <div className="indexed-content-header">
        <div>
          <p className="section-kicker text-xs font-semibold uppercase tracking-[0.14em]">
            Indexed content
          </p>
          <h2 className="mt-1 text-lg font-semibold text-white">Everything Relay has imported</h2>
          <p className="mt-1 text-xs text-white/50">
            Recent authorised items are grouped here so the source cards stay focused on connection
            status and sync controls.
          </p>
        </div>
        <FileText size={18} className="indexed-content-icon" />
      </div>
      <div className="indexed-content-grid">
        <section className="indexed-content-column">
          <div className="indexed-content-source">
            <span className="source-dot source-dot-confluence" />
            <strong>Atlassian</strong>
            <span>Confluence + Jira</span>
          </div>
          <AtlassianPreview preview={previews?.atlassian} />
        </section>
        <section className="indexed-content-column">
          <div className="indexed-content-source">
            <span className="source-dot source-dot-slack" />
            <strong>Slack</strong>
            <span>Messages and threads</span>
          </div>
          <PreviewList heading="Imported messages" items={previews?.slack} />
        </section>
        <section className="indexed-content-column">
          <div className="indexed-content-source">
            <span className="source-dot source-dot-google" />
            <strong>Google Drive</strong>
            <span>Documents and files</span>
          </div>
          <PreviewList heading="Imported files" items={previews?.googleDrive} />
        </section>
      </div>
    </GlassPanel>
  )
}
function PreviewList({
  heading,
  items,
}: {
  heading: string
  items: BackfillPreviewItem[] | undefined
}) {
  return (
    <section className="preview-section">
      <div className="preview-heading">
        <p>{heading}</p>
        {items && items.length > 0 && <span>{items.length}</span>}
      </div>
      {items === undefined ? (
        <p className="preview-empty">Updating imported content…</p>
      ) : items.length === 0 ? null : (
        <ul className="preview-list">
          {items.map((item) => (
            <li key={`${item.source}-${item.url ?? item.title}`} className="preview-item">
              <div className="min-w-0 flex-1">
                {item.url ? (
                  <a href={item.url} target="_blank" rel="noreferrer" className="preview-link">
                    {item.title}
                  </a>
                ) : (
                  <p className="preview-title">{item.title}</p>
                )}
                {item.snippet && <p className="preview-snippet">{item.snippet}</p>}
                {item.updatedAt && (
                  <p className="preview-date">Updated {formatPreviewDate(item.updatedAt)}</p>
                )}
              </div>
              {item.url && <ExternalLink size={12} className="preview-external" />}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
function PreviewEmpty() {
  return (
    <div className="preview-empty-state">
      <FileText size={16} />
      <div>
        <strong>No imported content yet</strong>
        <span>Run a sync to populate this source.</span>
      </div>
    </div>
  )
}
function formatPreviewDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? 'recently'
    : date.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })
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
