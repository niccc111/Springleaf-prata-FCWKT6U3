import type { ReactNode } from 'react';
import { Label } from '@/components/ui/label';

/** Label + control + inline error, wired for screen readers. */
export function Field({
  id,
  label,
  required,
  error,
  hint,
  children,
}: {
  id: string;
  label: string;
  required?: boolean;
  error?: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} required={required}>
        {label}
      </Label>
      {children}
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-[11px] font-medium text-destructive">
          {error}
        </p>
      ) : (
        hint && (
          <p id={`${id}-hint`} className="text-[11px] text-muted-foreground">
            {hint}
          </p>
        )
      )}
    </div>
  );
}
