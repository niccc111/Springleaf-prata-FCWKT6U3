import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react';
import { useEffect } from 'react';
import { cn } from '@/lib/utils';
import { useAppStore, type Toast } from '@/store';

const ICONS = {
  default: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  destructive: XCircle,
} as const;

const STYLES = {
  default: 'border-border bg-card',
  success: 'border-success/40 bg-success/10',
  warning: 'border-warning/40 bg-warning/10',
  destructive: 'border-destructive/40 bg-destructive/10',
} as const;

const ICON_STYLES = {
  default: 'text-muted-foreground',
  success: 'text-success',
  warning: 'text-warning',
  destructive: 'text-destructive',
} as const;

function ToastCard({ toast }: { toast: Toast }) {
  const dismissToast = useAppStore((s) => s.dismissToast);
  const Icon = ICONS[toast.variant];

  useEffect(() => {
    const duration = toast.durationMs ?? (toast.variant === 'destructive' ? 9000 : 5000);
    const timer = setTimeout(() => dismissToast(toast.id), duration);
    return () => clearTimeout(timer);
  }, [toast.id, toast.durationMs, toast.variant, dismissToast]);

  return (
    <div
      className={cn(
        'pointer-events-auto flex w-full max-w-sm animate-slide-in-right items-start gap-3 rounded-lg border p-3 shadow-lg',
        STYLES[toast.variant],
      )}
    >
      <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', ICON_STYLES[toast.variant])} />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium leading-snug">{toast.title}</p>
        {toast.description && (
          <p className="mt-0.5 break-words text-xs text-muted-foreground">{toast.description}</p>
        )}
      </div>
      <button
        type="button"
        onClick={() => dismissToast(toast.id)}
        className="rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
        aria-label="Dismiss notification"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

/** Live region so screen readers announce toasts as they arrive. */
export function Toaster() {
  const toasts = useAppStore((s) => s.toasts);
  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="false"
      className="pointer-events-none fixed bottom-4 right-4 z-[100] flex flex-col gap-2"
    >
      {toasts.map((toast) => (
        <ToastCard key={toast.id} toast={toast} />
      ))}
    </div>
  );
}
