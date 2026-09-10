import { Loader2, Route as RouteIcon } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ApiError } from '@/lib/api';

export interface LoginPageProps {
  onLogin: (email: string, password: string) => Promise<void>;
}

export function LoginPage({ onLogin }: LoginPageProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await onLogin(email.trim(), password);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Sign-in failed. Check your connection and retry.',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-full items-center justify-center bg-muted/40 p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <div className="rounded-xl bg-primary p-2.5 text-primary-foreground">
            <RouteIcon className="h-6 w-6" />
          </div>
          <h1 className="text-lg font-semibold">Route Optimisation Engine</h1>
          <p className="text-sm text-muted-foreground">Dispatcher console</p>
        </div>

        <form onSubmit={submit} className="panel space-y-4 p-5">
          {error && (
            <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs">
              {error}
            </p>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="email" required>
              Email
            </Label>
            <Input
              id="email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="dispatcher@roe.app"
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password" required>
              Password
            </Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </div>
          <Button type="submit" className="w-full" disabled={busy}>
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {busy ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>

        <p className="mt-4 text-center text-[11px] leading-relaxed text-muted-foreground">
          Demo accounts — dispatcher@roe.app / dispatch12345 · admin@roe.app / admin12345
        </p>
      </div>
    </div>
  );
}
