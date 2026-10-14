'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, Fingerprint, LockKeyhole, ShieldCheck, Sparkles } from 'lucide-react';
import { GlassPanel } from '@/components/GlassPanel';
import { ThemeToggle } from '@/components/ThemeToggle';
import { API_URL, devLogin, getAuthToken, setAuthToken } from '@/lib/api/client';

export default function LoginPage() {
  const router = useRouter();
  const [subject, setSubject] = useState('alice');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const existingToken = getAuthToken();

  async function handleLogin(event: React.FormEvent) {
    event.preventDefault(); setError(null); setLoading(true);
    try { const { token } = await devLogin(subject.trim() || 'alice'); setAuthToken(token); router.push('/query'); }
    catch { setError(`Sign in failed. Check that the API is reachable at ${API_URL}.`); }
    finally { setLoading(false); }
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center bg-background p-5">
      <ThemeToggle className="absolute right-5 top-5" />
      <div className="relative grid w-full max-w-4xl items-stretch gap-5 md:grid-cols-[.9fr_1.1fr]">
        <GlassPanel className="hidden flex-col justify-between p-8 md:flex" intensity="strong"><div><div className="flex items-center gap-2 text-sm font-semibold text-white"><span className="brand-glyph"><Sparkles size={16} /></span> relay</div><div className="section-kicker mt-16">A safer way to know</div><h1 className="mt-4 text-4xl font-semibold leading-[1.05] tracking-[-.06em] text-white">Answers that respect the boundary.</h1><p className="mt-5 text-sm leading-7 text-white/50">Connect your identity once. Search the workspace with confidence, knowing every answer is grounded and every citation is rechecked.</p></div><div className="flex items-center gap-2 text-xs text-teal-200"><ShieldCheck size={15} /> Permission-safe retrieval active</div></GlassPanel>
        <GlassPanel className="p-7 md:p-10" intensity="strong"><div className="mb-9"><div className="mb-7 flex items-center gap-2 text-sm font-semibold text-white md:hidden"><span className="brand-glyph"><Sparkles size={16} /></span> relay</div><div className="grid h-11 w-11 place-items-center rounded-lg bg-violet-300/10 text-violet-200"><Fingerprint size={22} /></div><h2 className="mt-6 text-2xl font-semibold tracking-tight text-white">Welcome back.</h2><p className="mt-2 text-sm leading-6 text-white/45">Sign in to search your connected sources.</p></div>{existingToken && <div className="mb-5 rounded-lg border border-teal-200/10 bg-teal-300/[.06] p-3 text-xs text-teal-100">You&apos;re already signed in. <button className="ml-1 underline" onClick={() => router.push('/query')}>Continue to Relay</button></div>}<form onSubmit={handleLogin} className="space-y-5"><label className="block"><span className="mb-2 block text-xs font-medium text-white/65">Workspace identity</span><input className="glass-input h-12 px-4 text-sm" value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="alice" autoComplete="username" /><span className="mt-2 block text-[11px] leading-5 text-white/35">Development sign in provisions a local enterprise identity for testing.</span></label>{error && <p className="rounded-lg border border-rose-200/10 bg-rose-300/[.06] p-3 text-xs text-rose-100">{error}</p>}<button type="submit" className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-lg text-sm font-semibold primary-glow" disabled={loading}>{loading ? 'Signing in…' : <>Sign in securely <ArrowRight size={16} /></>}</button></form><div className="mt-8 flex items-center justify-center gap-2 text-[11px] text-white/30"><LockKeyhole size={12} /> Your identity stays in this browser</div></GlassPanel>
      </div>
    </main>
  );
}
