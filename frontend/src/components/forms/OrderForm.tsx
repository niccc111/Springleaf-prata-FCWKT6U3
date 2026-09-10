/**
 * Manual order entry form (Requirements 3.1, 3.3-3.5).
 *
 * Client-side validation mirrors the server rules. On a 422 the server's field
 * errors are mapped onto the inputs and every entered value is preserved so the
 * dispatcher can correct and resubmit without retyping.
 */

import { Loader2 } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { ApiError } from '@/lib/api';
import {
  EMPTY_ORDER,
  orderPayload,
  validateOrder,
  type OrderFormValues,
} from '@/lib/validation';
import type { Order } from '@/types';

export interface OrderFormProps {
  onSubmit: (payload: ReturnType<typeof orderPayload>) => Promise<Order>;
  onCreated: (order: Order) => void;
}

export function OrderForm({ onSubmit, onCreated }: OrderFormProps) {
  const [values, setValues] = useState<OrderFormValues>(EMPTY_ORDER);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const set = (field: keyof OrderFormValues) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setValues((prev) => ({ ...prev, [field]: event.target.value }));
    setErrors((prev) => {
      if (!prev[field]) return prev;
      const next = { ...prev };
      delete next[field];
      return next;
    });
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);
    const clientErrors = validateOrder(values);
    if (Object.keys(clientErrors).length > 0) {
      setErrors(clientErrors);
      return;
    }
    setSubmitting(true);
    try {
      const order = await onSubmit(orderPayload(values));
      // Only clear the form once the record is safely persisted.
      setValues(EMPTY_ORDER);
      setErrors({});
      onCreated(order);
    } catch (error) {
      if (error instanceof ApiError) {
        setErrors(error.fieldErrors);
        setFormError(error.message);
      } else {
        setFormError('The order could not be saved. Your entries have been kept — try again.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3" noValidate>
      {formError && (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs">
          {formError}
        </p>
      )}

      <Field
        id="delivery_address"
        label="Delivery address"
        required
        error={errors.delivery_address}
        hint="The address is geocoded automatically; low-confidence matches are flagged for review."
      >
        <Input
          id="delivery_address"
          value={values.delivery_address}
          onChange={set('delivery_address')}
          placeholder="30 Raffles Place, Singapore 048622"
          maxLength={500}
          invalid={Boolean(errors.delivery_address)}
          aria-describedby={
            errors.delivery_address ? 'delivery_address-error' : 'delivery_address-hint'
          }
        />
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field id="cargo_weight_kg" label="Cargo weight (kg)" required error={errors.cargo_weight_kg}>
          <Input
            id="cargo_weight_kg"
            type="number"
            step="0.01"
            min="0.01"
            max="99999.99"
            value={values.cargo_weight_kg}
            onChange={set('cargo_weight_kg')}
            placeholder="12.50"
            invalid={Boolean(errors.cargo_weight_kg)}
          />
        </Field>
        <Field id="cargo_volume_m3" label="Cargo volume (m³)" error={errors.cargo_volume_m3}>
          <Input
            id="cargo_volume_m3"
            type="number"
            step="0.01"
            min="0"
            value={values.cargo_volume_m3}
            onChange={set('cargo_volume_m3')}
            placeholder="0.30"
            invalid={Boolean(errors.cargo_volume_m3)}
          />
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Field id="time_window_start" label="Window opens" error={errors.time_window_start}>
          <Input
            id="time_window_start"
            type="datetime-local"
            value={values.time_window_start}
            onChange={set('time_window_start')}
            invalid={Boolean(errors.time_window_start)}
          />
        </Field>
        <Field id="time_window_end" label="Window closes" error={errors.time_window_end}>
          <Input
            id="time_window_end"
            type="datetime-local"
            value={values.time_window_end}
            onChange={set('time_window_end')}
            invalid={Boolean(errors.time_window_end)}
          />
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Field id="priority" label="Priority" required error={errors.priority}>
          <Select id="priority" value={values.priority} onChange={set('priority')}>
            <option value="standard">Standard</option>
            <option value="priority">Priority</option>
          </Select>
        </Field>
        <Field
          id="service_duration_min"
          label="Service time (min)"
          error={errors.service_duration_min}
        >
          <Input
            id="service_duration_min"
            type="number"
            min="0"
            step="1"
            value={values.service_duration_min}
            onChange={set('service_duration_min')}
            invalid={Boolean(errors.service_duration_min)}
          />
        </Field>
      </div>

      <Button type="submit" disabled={submitting} className="w-full">
        {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
        {submitting ? 'Saving…' : 'Create order'}
      </Button>
    </form>
  );
}

