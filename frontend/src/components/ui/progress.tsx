import * as React from 'react';
import { cn } from '@/lib/utils';

export interface ProgressProps extends React.HTMLAttributes<HTMLDivElement> {
  value: number;
  max?: number;
  indicatorClassName?: string;
  label?: string;
}

/** Accessible determinate progress bar. */
export function Progress({
  value,
  max = 100,
  className,
  indicatorClassName,
  label,
  ...props
}: ProgressProps) {
  const clamped = Math.max(0, Math.min(value, max));
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(clamped)}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-label={label}
      className={cn('h-2 w-full overflow-hidden rounded-full bg-muted', className)}
      {...props}
    >
      <div
        className={cn('h-full rounded-full bg-primary transition-all duration-300', indicatorClassName)}
        style={{ width: `${(clamped / max) * 100}%` }}
      />
    </div>
  );
}
