import { AlertTriangle, HelpCircle, ShieldAlert } from 'lucide-react';
import { Progress } from '@/components/ui/progress';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn, formatUtilisation, loadLevel, type LoadLevel } from '@/lib/utils';

const BAR_STYLES: Record<LoadLevel, string> = {
  normal: 'bg-success',
  warning: 'bg-warning',
  critical: 'bg-destructive',
  unknown: 'bg-muted-foreground/40',
};

const TEXT_STYLES: Record<LoadLevel, string> = {
  normal: 'text-muted-foreground',
  warning: 'text-warning',
  critical: 'text-destructive',
  unknown: 'text-muted-foreground',
};

export interface LoadBarProps {
  label: string;
  utilisation: number | null;
  detail?: string;
  compact?: boolean;
}

/**
 * Load utilisation indicator (Requirement 9.2/9.3, Property 19):
 * green under 90%, amber + warning icon from 90%, red + critical icon at 100%.
 * Missing or invalid data shows "N/A" with a data-quality marker (9.5).
 */
export function LoadBar({ label, utilisation, detail, compact }: LoadBarProps) {
  const level = loadLevel(utilisation);
  const value = utilisation ?? 0;

  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="flex items-center gap-1 text-muted-foreground">
          {label}
          {level === 'warning' && (
            <Tooltip>
              <TooltipTrigger asChild>
                <AlertTriangle
                  className="h-3 w-3 text-warning"
                  data-testid="indicator-warning"
                  aria-label="Approaching capacity"
                />
              </TooltipTrigger>
              <TooltipContent>Vehicle is at 90% or more of capacity</TooltipContent>
            </Tooltip>
          )}
          {level === 'critical' && (
            <Tooltip>
              <TooltipTrigger asChild>
                <ShieldAlert
                  className="h-3 w-3 text-destructive"
                  data-testid="indicator-critical"
                  aria-label="Over capacity"
                />
              </TooltipTrigger>
              <TooltipContent>Vehicle is at or over capacity — an overload alert was raised</TooltipContent>
            </Tooltip>
          )}
          {level === 'unknown' && (
            <Tooltip>
              <TooltipTrigger asChild>
                <HelpCircle
                  className="h-3 w-3 text-muted-foreground"
                  data-testid="indicator-data-quality"
                  aria-label="Data unavailable"
                />
              </TooltipTrigger>
              <TooltipContent>Capacity or load data is missing for this route</TooltipContent>
            </Tooltip>
          )}
        </span>
        <span className={cn('font-medium tabular-nums', TEXT_STYLES[level])} data-testid={`load-${label.toLowerCase()}`}>
          {formatUtilisation(utilisation)}
        </span>
      </div>
      <Progress
        value={Math.min(value, 100)}
        indicatorClassName={BAR_STYLES[level]}
        className={cn(compact ? 'h-1.5' : 'h-2')}
        label={`${label} utilisation`}
      />
      {detail && !compact && <p className="text-[11px] text-muted-foreground">{detail}</p>}
    </div>
  );
}
