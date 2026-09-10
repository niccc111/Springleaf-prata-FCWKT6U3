/** Manual vehicle entry form (Requirements 3.2, 3.3-3.5). */

import { Loader2 } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { ApiError } from '@/lib/api';
import {
  EMPTY_VEHICLE,
  validateVehicle,
  vehiclePayload,
  type VehicleFormValues,
} from '@/lib/validation';
import type { Vehicle } from '@/types';

export interface VehicleFormProps {
  onSubmit: (payload: ReturnType<typeof vehiclePayload>) => Promise<Vehicle>;
  onCreated: (vehicle: Vehicle) => void;
}

export function VehicleForm({ onSubmit, onCreated }: VehicleFormProps) {
  const [values, setValues] = useState<VehicleFormValues>(EMPTY_VEHICLE);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const set = (field: keyof VehicleFormValues) => (event: React.ChangeEvent<HTMLInputElement>) => {
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
    const clientErrors = validateVehicle(values);
    if (Object.keys(clientErrors).length > 0) {
      setErrors(clientErrors);
      return;
    }
    setSubmitting(true);
    try {
      const vehicle = await onSubmit(vehiclePayload(values));
      setValues(EMPTY_VEHICLE);
      setErrors({});
      onCreated(vehicle);
    } catch (error) {
      if (error instanceof ApiError) {
        const mapped = error.fieldErrors;
        // Nested depot errors come back as depot_location.latitude.
        setErrors({
          ...mapped,
          depot_latitude: mapped['depot_location.latitude'] ?? mapped.latitude ?? '',
          depot_longitude: mapped['depot_location.longitude'] ?? mapped.longitude ?? '',
        });
        setFormError(error.message);
      } else {
        setFormError('The vehicle could not be saved. Your entries have been kept — try again.');
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

      <Field id="registration" label="Registration" required error={errors.registration}>
        <Input
          id="registration"
          value={values.registration}
          onChange={set('registration')}
          placeholder="SGX1234A"
          maxLength={20}
          invalid={Boolean(errors.registration)}
        />
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field
          id="capacity_weight_kg"
          label="Weight capacity (kg)"
          required
          error={errors.capacity_weight_kg}
        >
          <Input
            id="capacity_weight_kg"
            type="number"
            step="0.01"
            min="0.01"
            value={values.capacity_weight_kg}
            onChange={set('capacity_weight_kg')}
            placeholder="1200"
            invalid={Boolean(errors.capacity_weight_kg)}
          />
        </Field>
        <Field id="capacity_volume_m3" label="Volume capacity (m³)" error={errors.capacity_volume_m3}>
          <Input
            id="capacity_volume_m3"
            type="number"
            step="0.01"
            min="0.01"
            value={values.capacity_volume_m3}
            onChange={set('capacity_volume_m3')}
            placeholder="12"
            invalid={Boolean(errors.capacity_volume_m3)}
          />
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Field id="depot_latitude" label="Depot latitude" required error={errors.depot_latitude}>
          <Input
            id="depot_latitude"
            type="number"
            step="0.000001"
            value={values.depot_latitude}
            onChange={set('depot_latitude')}
            placeholder="1.2790"
            invalid={Boolean(errors.depot_latitude)}
          />
        </Field>
        <Field id="depot_longitude" label="Depot longitude" required error={errors.depot_longitude}>
          <Input
            id="depot_longitude"
            type="number"
            step="0.000001"
            value={values.depot_longitude}
            onChange={set('depot_longitude')}
            placeholder="103.8090"
            invalid={Boolean(errors.depot_longitude)}
          />
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Field
          id="operating_hours_start"
          label="Shift starts"
          required
          error={errors.operating_hours_start}
        >
          <Input
            id="operating_hours_start"
            type="time"
            value={values.operating_hours_start}
            onChange={set('operating_hours_start')}
            invalid={Boolean(errors.operating_hours_start)}
          />
        </Field>
        <Field
          id="operating_hours_end"
          label="Shift ends"
          required
          error={errors.operating_hours_end}
        >
          <Input
            id="operating_hours_end"
            type="time"
            value={values.operating_hours_end}
            onChange={set('operating_hours_end')}
            invalid={Boolean(errors.operating_hours_end)}
          />
        </Field>
      </div>

      <Field id="driver_name" label="Driver" error={errors.driver_name}>
        <Input
          id="driver_name"
          value={values.driver_name}
          onChange={set('driver_name')}
          placeholder="A. Tan"
          maxLength={200}
        />
      </Field>

      <Button type="submit" disabled={submitting} className="w-full">
        {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
        {submitting ? 'Saving…' : 'Create vehicle'}
      </Button>
    </form>
  );
}
