import Link from 'next/link'
import {
  ArrowRight,
  Check,
  LockKeyhole,
  MessageSquareText,
  ShieldCheck,
  Sparkles,
  Zap,
} from 'lucide-react'
import { GlassPanel } from '@/components/GlassPanel'

const capabilities = [
  {
    icon: ShieldCheck,
    title: 'Permission-aware',
    copy: 'Only content you can access is ever used to answer.',
  },
  {
    icon: Zap,
    title: 'Cross-source search',
    copy: 'Bring together the answers hiding across your connected tools.',
  },
  {
    icon: LockKeyhole,
    title: 'Auditable by default',
    copy: 'Every answer carries its evidence and a tamper-evident trail.',
  },
]

export default function Home() {
  return (
    <div className="mx-auto max-w-6xl">
      <section className="grid items-center gap-12 py-8 xl:grid-cols-[1.1fr_.9fr] xl:py-16">
        <div>
          <div className="section-kicker flex items-center gap-2">
            <Sparkles size={13} /> Your organisation&apos;s signal, in one place
          </div>
          <h1 className="page-title max-w-2xl">
            The calm layer over your company&apos;s knowledge.
          </h1>
          <p className="page-subtitle max-w-xl">
            Relay searches the sources you already trust, checks your access in real time, and gives
            you an answer you can confidently share.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href="/query"
              className="inline-flex h-12 items-center gap-2 rounded-xl px-5 text-sm font-semibold primary-glow"
            >
              <MessageSquareText size={17} /> Ask a question <ArrowRight size={16} />
            </Link>
            <Link
              href="/settings"
              className="inline-flex h-12 items-center rounded-xl px-5 text-sm font-medium ghost-glass"
            >
              Connect your sources
            </Link>
          </div>
          <div className="mt-8 flex items-center gap-3 text-xs muted-text">
            <span className="status-pulse" /> Secure retrieval is active{' '}
            <span className="mx-1 opacity-40">·</span> No content leaves your permission boundary
          </div>
        </div>

        <GlassPanel className="min-h-[360px] p-7" intensity="strong" interactive>
          <div className="flex items-center justify-between">
            <div>
              <div className="section-kicker">A better starting point</div>
              <h2 className="mt-2 text-xl font-semibold tracking-tight text-white">
                Ask the whole workspace.
              </h2>
            </div>
            <div className="grid h-10 w-10 place-items-center rounded-lg border border-violet-200/20 bg-violet-300/10 text-violet-200">
              <Sparkles size={18} />
            </div>
          </div>
          <div className="mt-8 rounded-lg border border-white/10 bg-black/20 p-4">
            <p className="text-[11px] font-medium text-violet-200/70">Try asking</p>
            <p className="mt-3 text-sm leading-6 text-white/85">
              What did the team decide about the Q4 migration plan?
            </p>
            <div className="mt-5 flex items-center gap-2 text-[10px] text-white/45">
              <span className="source-chip">
                <span className="source-dot source-dot-confluence" /> Confluence
              </span>
              <span className="source-chip">
                <span className="source-dot source-dot-google" /> Drive
              </span>
              <span className="source-chip">
                <span className="source-dot source-dot-slack" /> Slack
              </span>
            </div>
          </div>
          <div className="mt-4 flex items-center gap-3 rounded-lg border border-teal-200/10 bg-teal-300/[.05] p-4">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-teal-300/10 text-teal-200">
              <Check size={16} />
            </div>
            <div>
              <p className="text-xs font-semibold text-white/80">Verified answer</p>
              <p className="mt-1 text-[11px] leading-5 text-white/45">
                Evidence is rechecked before you see it.
              </p>
            </div>
          </div>
        </GlassPanel>
      </section>

      <section className="grid gap-3 pb-12 md:grid-cols-3">
        {capabilities.map(({ icon: Icon, title, copy }) => (
          <GlassPanel key={title} className="p-5" interactive>
            <Icon size={18} className="text-violet-200" />
            <h3 className="mt-5 text-sm font-semibold text-white">{title}</h3>
            <p className="mt-2 text-xs leading-5 text-white/45">{copy}</p>
          </GlassPanel>
        ))}
      </section>
    </div>
  )
}
