/**
 * @vitest-environment jsdom
 */

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getConnections: vi.fn(),
  syncConnection: vi.fn(),
  startOAuth: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock('@/lib/api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/lib/api/client')>()
  return {
    ...original,
    getConnections: api.getConnections,
    syncConnection: api.syncConnection,
    startOAuth: api.startOAuth,
  }
})

import SettingsPage from './page'

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  ;(
    globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
  ).IS_REACT_ACT_ENVIRONMENT = true
  api.getConnections.mockReset()
  api.syncConnection.mockReset()
  api.startOAuth.mockReset()
  window.localStorage.clear()
  window.localStorage.setItem('auth_token', 'test-token')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

async function renderSettings(): Promise<void> {
  await act(async () => {
    root.render(<SettingsPage />)
  })
  await act(async () => new Promise((resolve) => window.setTimeout(resolve, 0)))
}

test('labels fixture connections as demo data and offers no live actions', async () => {
  api.getConnections.mockResolvedValue({
    dataMode: 'fixture',
    connections: [
      {
        source: 'google_drive',
        connected: true,
        display_name: 'Demo Google Drive',
        connectionId: 'fixture-connection',
        mode: 'fixture',
        canSync: false,
      },
    ],
  })

  await renderSettings()

  expect(container.textContent).toContain('Demo data')
  expect(container.textContent).toContain('synthetic fixture data')
  expect(container.textContent).not.toContain('Sync now')
  const connectButtons = Array.from(container.querySelectorAll('button')).filter((button) =>
    button.textContent?.includes('Connect'),
  )
  expect(connectButtons).toHaveLength(3)
  expect(connectButtons.every((button) => button.disabled)).toBe(true)
})

test('labels live connections and queues an exact connection sync', async () => {
  api.getConnections.mockResolvedValue({
    dataMode: 'live',
    connections: [
      {
        source: 'google_drive',
        connected: true,
        display_name: 'Jess Drive',
        connectionId: 'drive-connection',
        mode: 'delegated_oauth',
        canSync: true,
      },
    ],
  })
  api.syncConnection.mockResolvedValue({
    connectionId: 'drive-connection',
    status: 'queued',
  })

  await renderSettings()

  expect(container.textContent).toContain('Live OAuth')
  const syncButton = Array.from(container.querySelectorAll('button')).find(
    (button) => button.textContent?.trim() === 'Sync now',
  )
  expect(syncButton).toBeDefined()

  await act(async () => syncButton?.click())

  expect(api.syncConnection).toHaveBeenCalledWith('drive-connection')
  expect(container.textContent).toContain('Google Drive sync queued.')
})
