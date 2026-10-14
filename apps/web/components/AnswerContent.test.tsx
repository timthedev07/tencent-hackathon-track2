/**
 * @vitest-environment jsdom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

vi.mock('next/link', () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a>,
}));

import { AnswerContent } from './AnswerContent';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

test('turns recognised answer items into coloured, linked badges', async () => {
  const evidenceId = 'e1a2b3c4-d5e6-7f80-9a0b-c1d2e3f4a5b6';
  const reference = 'evi_e1a2b3c4d5e6';

  await act(async () => {
    root.render(
      <AnswerContent
        answer={`The decision is due on 14 October 2026 at 14:30. Evidence IDs [${reference}], [${reference}] and [${reference}] support KAN-1 and KAN-2. https://example.com/brief`}
        citations={[{ evidenceId, source: 'jira', label: 'KAN-1 release decision', url: 'https://acme.atlassian.net/browse/KAN-1' }]}
      />,
    );
  });

  expect(container.textContent).not.toContain(`[${reference}]`);
  expect(container.querySelectorAll('a[href="/evidence/e1a2b3c4-d5e6-7f80-9a0b-c1d2e3f4a5b6"]')).toHaveLength(3);
  expect(container.querySelector('a[href="https://acme.atlassian.net/browse/KAN-1"]')?.textContent).toContain('KAN-1');
  expect(container.querySelector('a[href="https://acme.atlassian.net/browse/KAN-2"]')?.textContent).toContain('KAN-2');
  expect(container.textContent).toContain('14 October 2026');
  expect(container.textContent).toContain('14:30');
});
