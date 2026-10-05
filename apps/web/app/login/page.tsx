'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { devLogin, setAuthToken, getAuthToken } from '@/lib/api/client';

export default function LoginPage() {
  const router = useRouter();
  const [subject, setSubject] = useState('alice');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // If already logged in, show a "continue" option
  const existingToken = getAuthToken();

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { token } = await devLogin(subject);
      setAuthToken(token);
      router.push('/');
    } catch {
      setError('Login failed. Is the API running on localhost:8000?');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Dev Login</CardTitle>
        </CardHeader>
        <CardContent>
          {existingToken && (
            <div className="mb-4">
              <p className="mb-2 text-sm text-muted-foreground">
                You&apos;re already logged in.
              </p>
              <Button className="w-full" onClick={() => router.push('/')}>
                Continue to app
              </Button>
            </div>
          )}
          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label className="mb-1 block text-sm font-medium">Username</label>
              <Input
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                placeholder="alice"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Enter any username. A dev user will be auto-provisioned.
              </p>
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? 'Signing in...' : 'Sign in (dev)'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
