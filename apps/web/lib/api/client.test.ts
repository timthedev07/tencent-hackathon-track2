import { afterEach, expect, test, vi } from 'vitest';

import { ApiClientError, createQuery } from './client';

afterEach(() => {
  vi.unstubAllGlobals();
});

test('surfaces the error message from a FastAPI detail response', async () => {
  vi.stubGlobal('window', { localStorage: { getItem: () => null } });
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
  );

  await expect(createQuery('hello')).rejects.toEqual(
    new ApiClientError('AUTH_INVALID_TOKEN', 'Authentication required.', 401),
  );
});
