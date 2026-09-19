/** App shell for the local dispatcher workspace. */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect, useMemo } from 'react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Toaster } from '@/components/ui/toast';
import { useRoeSocket } from '@/hooks/useRoeSocket';
import { ApiError } from '@/lib/api';
import { DispatcherConsole } from '@/pages/DispatcherConsole';
import { useAppStore } from '@/store';
import type { User } from '@/types';

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

const LOCAL_DISPATCHER: User = {
  user_id: 'local-dispatcher',
  email: 'dispatcher@roe.app',
  full_name: 'Duty Dispatcher',
  role: 'dispatcher',
  active: true,
  created_at: '',
  updated_at: '',
};

function LocalWorkspace() {
  const setSession = useAppStore((s) => s.setSession);

  useEffect(() => {
    setSession({ access_token: '', refresh_token: '', user: LOCAL_DISPATCHER });
  }, [setSession]);

  useRoeSocket(null);
  return <DispatcherConsole />;
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
          <LocalWorkspace />
          <Toaster />
        </div>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
