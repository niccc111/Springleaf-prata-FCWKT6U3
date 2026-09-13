/** App shell: query/tooltip providers, live event stream, and the console. */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect, useMemo } from 'react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Toaster } from '@/components/ui/toast';
import { useRoeSocket } from '@/hooks/useRoeSocket';
import { ApiError } from '@/lib/api';
import { DispatcherConsole } from '@/pages/DispatcherConsole';

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

/** The console is open: there is no sign-in step and no session to restore. */
function Console() {
  useRoeSocket();
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
          <Console />
          <Toaster />
        </div>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
