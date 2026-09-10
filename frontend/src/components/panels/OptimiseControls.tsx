/**
 * Optimisation controls with live progress (Requirements 5.8, 12.4-12.7, 18.6).
 */

import { AlertCircle, Loader2, PlayCircle, RefreshCcwDot } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { pluralise } from '@/lib/utils';
import { useAppStore } from '@/store';

export interface OptimiseControlsProps {
  onOptimise: (reoptimise: boolean) => void;
  running: boolean;
  hasRoutes: boolean;
}

export function OptimiseControls({ onOptimise, running, hasRoutes }: OptimiseControlsProps) {
  const progress = useAppStore((s) => s.runProgress);
  const lastRun = useAppStore((s) => s.lastRun);
  const pendingOrders = useAppStore((s) => s.pendingOrders);
  const reoptimiseHint = useAppStore((s) => s.reoptimiseHint);

  return (
    <div className="space-y-2 border-b border-border p-3">
      <div className="flex gap-2">
        <Button
          className="flex-1"
          onClick={() => onOptimise(false)}
          disabled={running}
          title="Assign all unassigned orders to available vehicles"
        >
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}
          {running ? 'Optimising…' : 'Optimise'}
        </Button>
        <Button
          variant="outline"
          onClick={() => onOptimise(true)}
          disabled={running || !hasRoutes}
          title="Re-plan every unlocked route, preserving locked ones"
        >
          <RefreshCcwDot className="h-4 w-4" />
          Re-optimise
        </Button>
      </div>

      {running && (
        <div className="space-y-1" role="status" aria-live="polite">
          <Progress
            value={progress?.pct ?? 5}
            label="Optimisation progress"
            className="h-1.5"
          />
          <p className="text-[11px] text-muted-foreground">
            {progress?.message ?? 'Starting optimisation…'} ({progress?.pct ?? 0}%)
          </p>
        </div>
      )}

      {!running && (pendingOrders > 0 || reoptimiseHint) && (
        <button
          type="button"
          onClick={() => onOptimise(hasRoutes)}
          className="flex w-full items-start gap-2 rounded-md border border-primary/30 bg-primary/5 p-2 text-left text-[11px] leading-relaxed transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
          <span>
            {reoptimiseHint ??
              `${pluralise(pendingOrders, 'order')} waiting to be planned.`}{' '}
            <span className="font-medium underline">
              {hasRoutes ? 'Re-optimise now' : 'Optimise now'}
            </span>
          </span>
        </button>
      )}

      {!running && lastRun && lastRun.status === 'completed' && (
        <p className="text-[11px] text-muted-foreground">
          Last run: {pluralise(lastRun.routes_created ?? 0, 'route')},{' '}
          {lastRun.orders_assigned ?? 0} assigned, {lastRun.orders_unassigned ?? 0} unassigned
          {lastRun.locked_excluded_count
            ? `, ${pluralise(lastRun.locked_excluded_count, 'locked route')} excluded`
            : ''}
          {lastRun.duration_seconds ? ` · ${lastRun.duration_seconds.toFixed(1)}s` : ''}
        </p>
      )}

      {!running && lastRun && lastRun.status !== 'completed' && lastRun.error_message && (
        <p className="flex items-start gap-1.5 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-[11px] leading-relaxed">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" aria-hidden="true" />
          {lastRun.error_message}
        </p>
      )}
    </div>
  );
}
