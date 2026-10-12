import { afterEach, expect, test, vi } from 'vitest'

import { ApiClientError, createQuery, syncConnection } from './client'

afterEach(() => {
  vi.unstubAllGlobals()
})

test('surfaces the error message from a FastAPI detail response', async () => {
  vi.stubGlobal('window', { localStorage: { getItem: () => null } })
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            error: {
              code: 'AUTH_INVALID_TOKEN',
              message: 'Authentication required.',
            },
          },
        }),
        { status: 401, headers: { 'Content-Type': 'application/json' } },
      ),
    ),
  )

  await expect(createQuery('hello')).rejects.toEqual(
    new ApiClientError('AUTH_INVALID_TOKEN', 'Authentication required.', 401),
  )
})

test('queues an authenticated sync for the exact connection', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ connectionId: 'connection-123', status: 'queued' }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
  vi.stubGlobal('window', { localStorage: { getItem: () => 'token-123' } })
  vi.stubGlobal('fetch', fetchMock)

  await expect(syncConnection('connection-123')).resolves.toEqual({
    connectionId: 'connection-123',
    status: 'queued',
  })
  expect(fetchMock).toHaveBeenCalledWith(
    'http://localhost:8000/v1/oauth/connections/connection-123/sync',
    expect.objectContaining({
      method: 'POST',
      headers: expect.objectContaining({ Authorization: 'Bearer token-123' }),
    }),
  )
})
