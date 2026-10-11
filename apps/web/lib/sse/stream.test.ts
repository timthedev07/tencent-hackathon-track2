import { afterEach, expect, test, vi } from 'vitest'

import { SseStream } from './stream'

afterEach(() => {
  vi.unstubAllGlobals()
})

test('delivers JSON progress and terminal events without reconnecting', async () => {
  vi.stubGlobal('window', { localStorage: { getItem: () => 'test-token' } })

  const wire = [
    'event: query.authenticated\r\ndata: {"queryId":"query-1","message":"Checking your identity"}\r\n\r\n',
    'event: query.denied\r\ndata: {"queryId":"query-1","errorCode":"NO_SEARCHABLE_CONTENT"}\r\n\r\n',
  ].join('')
  const bytes = new TextEncoder().encode(wire)
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(bytes.slice(0, 28))
      controller.enqueue(bytes.slice(28, 79))
      controller.enqueue(bytes.slice(79))
      controller.close()
    },
  })
  const fetchMock = vi.fn().mockResolvedValue(new Response(stream, { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)

  const onEvent = vi.fn()
  const onError = vi.fn()
  const onClose = vi.fn()

  await new SseStream('query-1', { onEvent, onError, onClose }).connect()

  expect(onEvent).toHaveBeenCalledTimes(2)
  expect(onEvent).toHaveBeenNthCalledWith(1, {
    event: 'query.authenticated',
    data: { queryId: 'query-1', message: 'Checking your identity', errorCode: undefined },
  })
  expect(onEvent).toHaveBeenNthCalledWith(2, {
    event: 'query.denied',
    data: { queryId: 'query-1', message: undefined, errorCode: 'NO_SEARCHABLE_CONTENT' },
  })
  expect(onError).not.toHaveBeenCalled()
  expect(onClose).toHaveBeenCalledOnce()
  expect(fetchMock).toHaveBeenCalledOnce()
})

test('reports malformed progress data immediately instead of reconnecting', async () => {
  vi.stubGlobal('window', { localStorage: { getItem: () => 'test-token' } })
  const malformed = "event: query.denied\r\ndata: {'queryId': 'query-1'}\r\n\r\n"
  const fetchMock = vi.fn().mockResolvedValue(new Response(malformed, { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)

  const onEvent = vi.fn()
  const onError = vi.fn()
  await new SseStream('query-1', { onEvent, onError }).connect()

  expect(onEvent).not.toHaveBeenCalled()
  expect(onError).toHaveBeenCalledWith(expect.objectContaining({ code: 'INVALID_STREAM_EVENT' }))
  expect(fetchMock).toHaveBeenCalledOnce()
})
