import type { Metadata } from 'next'
import './globals.css'
import { AppShell } from '@/components/AppShell'

export const metadata: Metadata = {
  title: 'Enterprise AI Assistant',
  description: 'Permission-safe cross-platform enterprise AI assistant',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  )
}
