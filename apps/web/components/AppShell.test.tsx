/**
 * @vitest-environment jsdom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

const navigation = vi.hoisted(() => ({
  pathname: '/query',
  replace: vi.fn(),
  push: vi.fn(),
}));
const api = vi.hoisted(() => ({
  getAuditHistory: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ replace: navigation.replace, push: navigation.push }),
}));

vi.mock('next/link', () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  getAuditHistory: api.getAuditHistory,
}));

import { AppShell } from './AppShell';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  navigation.pathname = '/query';
  navigation.replace.mockReset();
  navigation.push.mockReset();
  api.getAuditHistory.mockResolvedValue({});
  window.localStorage.clear();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

test('redirects a guest away from protected pages without rendering protected content', async () => {
  await act(async () => {
    root.render(
      <AppShell>
        <div>Protected query form</div>
      </AppShell>,
    );
  });
  await act(async () => new Promise((resolve) => window.setTimeout(resolve, 0)));

  expect(navigation.replace).toHaveBeenCalledWith('/login');
  expect(container.textContent).not.toContain('Protected query form');
});

test('renders the public homepage immediately for a guest', async () => {
  navigation.pathname = '/';

  await act(async () => {
    root.render(
      <AppShell>
        <div>Public homepage</div>
      </AppShell>,
    );
  });

  expect(container.textContent).toContain('Public homepage');
  expect(navigation.replace).not.toHaveBeenCalled();
});

test('keeps compliance highlighted on an audit-detail subroute', async () => {
  navigation.pathname = '/compliance/query-123';
  window.localStorage.setItem('auth_token', 'dev-compliance');

  await act(async () => {
    root.render(
      <AppShell>
        <div>Compliance detail</div>
      </AppShell>,
    );
  });
  await act(async () => new Promise((resolve) => window.setTimeout(resolve, 0)));
  await act(async () => Promise.resolve());

  expect(container.querySelector('a[href="/compliance"]')?.className).toContain('sidebar-link-active');
});
