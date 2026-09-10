import * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * A native select styled to match the design system. Native is deliberate: it
 * gives dispatchers the platform keyboard model and works on touch devices in
 * the cab without extra JavaScript.
 */
export type SelectProps = React.SelectHTMLAttributes<HTMLSelectElement> & {
  invalid?: boolean;
};

const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, invalid, children, ...props }, ref) => (
    <select
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50',
        invalid && 'border-destructive focus-visible:ring-destructive',
        className,
      )}
      {...props}
    >
      {children}
    </select>
  ),
);
Select.displayName = 'Select';

export { Select };
