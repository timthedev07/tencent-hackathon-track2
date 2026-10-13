'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import {
  ArrowUpRight,
  CircleHelp,
  LogOut,
  MessageSquareText,
  Settings,
  ShieldCheck,
  Sparkles,
  UserRound,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { clearAuthToken, getAuthToken } from '@/lib/api/client';
import { GlassPanel } from './GlassPanel';

const navigation = [
  { href: '/query', label: 'Ask the assistant', icon: MessageSquareText },
  { href: '/settings', label: 'Connected sources', icon: Settings },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [authStatus, setAuthStatus] = useState<'checking' | 'authenticated' | 'guest'>('checking');
  const [accountLabel, setAccountLabel] = useState('Guest');
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const publicPath = pathname === '/' || pathname === '/login';

  useEffect(() => {
    const refresh = window.setTimeout(() => {
      const token = getAuthToken();
      const devSubject = token?.startsWith('dev-') ? token.slice(4) : null;
      const signedIn = Boolean(token);
      setAuthStatus(signedIn ? 'authenticated' : 'guest');
      if (!signedIn && !publicPath) router.replace('/login');
      setAccountLabel(
        devSubject
          ? devSubject.charAt(0).toUpperCase() + devSubject.slice(1)
          : token
            ? 'Signed in'
            : 'Guest',
      );
    }, 0);
    return () => window.clearTimeout(refresh);
  }, [pathname, publicPath, router]);

  if (pathname === '/login') return <>{children}</>;
  if (!publicPath && (authStatus === 'checking' || authStatus === 'guest')) return null;

  function handleSignOut() {
    clearAuthToken();
    setAuthStatus('guest');
    setAccountLabel('Guest');
    router.push('/login');
  }

  return (
    <div className="app-frame">
      <aside className="app-sidebar">
        <Link href="/" className="brand-mark" aria-label="Relay home">
          <span className="brand-glyph"><Sparkles size={17} strokeWidth={2.5} /></span>
          <span>
            <span className="brand-name">relay</span>
            <span className="brand-caption">Secure intelligence</span>
          </span>
        </Link>

        <div className="sidebar-label">Workspace</div>
        <nav className="sidebar-nav" aria-label="Main navigation">
          {navigation.map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href || (item.href === '/query' && pathname.startsWith('/evidence'));
            return (
              <Link key={item.href} href={item.href} className={cn('sidebar-link', active && 'sidebar-link-active')}>
                <Icon size={17} />
                <span>{item.label}</span>
                {active && <span className="sidebar-link-dot" />}
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-spacer" />

        <GlassPanel className="sidebar-trust-card" intensity="strong">
          <div className="trust-icon"><ShieldCheck size={16} /></div>
          <div>
            <p className="trust-title">Permission-safe by design</p>
            <p className="trust-copy">Every source is rechecked before it is shown.</p>
          </div>
        </GlassPanel>

        <div className="sidebar-help">
          <CircleHelp size={15} />
          <span>Need a hand?</span>
          <ArrowUpRight size={13} className="ml-auto" />
        </div>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <div className="topbar-context">
            <span className="status-pulse" />
            <span>All systems operational</span>
          </div>
          <div className="topbar-actions">
            <span className="workspace-name">Acme workspace</span>
            <div className="account-wrap">
              <button className="account-button" onClick={() => setAccountMenuOpen((open) => !open)} aria-expanded={accountMenuOpen}>
                <span className="account-avatar"><UserRound size={15} /></span>
                <span className="account-label">{authStatus === 'authenticated' ? accountLabel : 'Guest'}</span>
              </button>
              {accountMenuOpen && (
                <GlassPanel outerClassName="account-menu" intensity="strong">
                  {authStatus === 'authenticated' ? (
                    <button className="account-menu-item" onClick={handleSignOut}><LogOut size={15} /> Sign out</button>
                  ) : (
                    <Link className="account-menu-item" href="/login"><UserRound size={15} /> Sign in</Link>
                  )}
                </GlassPanel>
              )}
            </div>
          </div>
        </header>
        <main className="app-content">{children}</main>
      </div>
    </div>
  );
}
