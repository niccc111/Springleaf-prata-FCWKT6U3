/** App shell: session bootstrap, auth guard, and the query/tooltip providers. */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Toaster } from '@/components/ui/toast';
import { LoadingState } from '@/components/ui/states';
import { useRoeSocket } from '@/hooks/useRoeSocket';
import { api, ApiError, setAccessToken, setUnauthorisedHandler } from '@/lib/api';
import { DispatcherConsole } from '@/pages/DispatcherConsole';
import { LoginPage } from '@/pages/LoginPage';
import { readStoredTokens, useAppStore, writeStoredTokens } from '@/store';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status < 500) return false;
        return failureCount < 2;
      },
      refetchOnWindowFocus: true,
    },
  },
});

function AuthGuard() {
  const user = useAppStore((s) => s.user);
  const accessToken = useAppStore((s) => s.accessToken);
  const setSession = useAppStore((s) => s.setSession);
  const pushToast = useAppStore((s) => s.pushToast);
  const [restoring, setRestoring] = useState(true);

  const signOut = useCallback(() => {
    setSession(null);
    setAccessToken(null);
    writeStoredTokens(null);
    queryClient.clear();
  }, [setSession]);

  // Restore a stored session; a stale token is refreshed before use.
  useEffect(() => {
    let cancelled = false;

    const restore = async () => {
      const stored = readStoredTokens();
      if (!stored) {
        setRestoring(false);
        return;
      }
      setAccessToken(stored.access_token);
      try {
        const me = await api.me();
        if (cancelled) return;
        setSession({ ...stored, user: me });
      } catch {
        try {
          const refreshed = await api.refresh(stored.refresh_token);
          if (cancelled) return;
          setAccessToken(refreshed.access_token);
          setSession(refreshed);
          writeStoredTokens(refreshed);
        } catch {
          if (!cancelled) signOut();
        }
      } finally {
        if (!cancelled) setRestoring(false);
      }
    };

    void restore();
    return () => {
      cancelled = true;
    };
  }, [setSession, signOut]);

  // A 401 from any endpoint ends the session cleanly (Requirement 17.4).
  useEffect(() => {
    setUnauthorisedHandler(() => {
      if (useAppStore.getState().user) {
        signOut();
        pushToast({
          title: 'Session expired',
          description: 'Sign in again to continue.',
          variant: 'warning',
        });
      }
    });
    return () => setUnauthorisedHandler(null);
  }, [signOut, pushToast]);

  useRoeSocket(accessToken);

  const handleLogin = useCallback(
    async (email: string, password: string) => {
      const tokens = await api.login(email, password);
      setAccessToken(tokens.access_token);
      setSession(tokens);
      writeStoredTokens(tokens);
    },
    [setSession],
  );

  if (restoring) {
    return (
      <div className="flex h-full items-center justify-center">
        <LoadingState label="Restoring your session…" />
      </div>
    );
  }

  if (!user) return <LoginPage onLogin={handleLogin} />;
  return <DispatcherConsole onSignOut={signOut} />;
}

export default function App() {
  const theme = useMemo(() => {
    if (typeof window === 'undefined') return 'light';
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
  }, [theme]);

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={200}>
        <div className="h-full">
          <AuthGuard />
          <Toaster />
        </div>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
