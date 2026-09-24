import Link from 'next/link';
import { Button } from '@/components/ui/button';

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <h1 className="text-4xl font-bold tracking-tight">Enterprise AI Assistant</h1>
      <p className="mt-4 text-muted-foreground">Permission-safe cross-platform retrieval</p>
      <div className="mt-8 flex space-x-4">
        <Button asChild><Link href="/query">Ask a question</Link></Button>
        <Button variant="outline" asChild><Link href="/settings">Connection settings</Link></Button>
      </div>
    </main>
  );
}
