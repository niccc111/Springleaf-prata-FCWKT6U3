import type { LucideIcon } from 'lucide-react';
import { AlertCircle, Loader2, RefreshCw } from 'lucide-react';
import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

/** Shared loading / empty / error presentations used across every panel. */

export function LoadingState({ label = 'Loading…', className }: { label?: string; className?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn('flex flex-col items-center justify-center gap-2 p-8 text-center', className)}
    >
      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      <p className="text-sm text-muted-foreground">{label}</p>
    </div>
  );
}

export function SkeletonList({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-3" aria-hidden="true">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="panel space-y-2 p-3">
          <div className="skeleton h-4 w-1/3" />
          <div className="skeleton h-2 w-full" />
          <div className="skeleton h-3 w-2/3" />
        </div>
      ))}
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-3 p-8 text-center', className)}>
      <div className="rounded-full bg-muted p-3">
        <Icon className="h-5 w-5 text-muted-foreground" />
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium">{title}</p>
        {description && (
          <p className="mx-auto max-w-xs text-xs leading-relaxed text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {action}
    </div>
  );
}

export function ErrorState({
  title = 'Something went wrong',
  description,
  onRetry,
  className,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn('flex flex-col items-center justify-center gap-3 p-8 text-center', className)}
    >
      <div className="rounded-full bg-destructive/10 p-3">
        <AlertCircle className="h-5 w-5 text-destructive" />
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium">{title}</p>
        {description && (
          <p className="mx-auto max-w-sm text-xs leading-relaxed text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5" />
          Try again
        </Button>
      )}
    </div>
  );
}
