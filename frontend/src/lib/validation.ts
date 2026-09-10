/**
 * Client-side validation mirroring the server rules (Requirements 3.1-3.3).
 *
 * Keeping these pure and separate from the components makes them directly
 * testable and keeps the forms focused on presentation.
 */

export interface OrderFormValues {
  delivery_address: string;
  cargo_weight_kg: string;
  cargo_volume_m3: string;
  time_window_start: string;
  time_window_end: string;
  priority: 'standard' | 'priority';
  service_duration_min: string;
}

export interface VehicleFormValues {
  registration: string;
  capacity_weight_kg: string;
  capacity_volume_m3: string;
  depot_latitude: string;
  depot_longitude: string;
  operating_hours_start: string;
  operating_hours_end: string;
  driver_name: string;
}

export const EMPTY_ORDER: OrderFormValues = {
  delivery_address: '',
  cargo_weight_kg: '',
  cargo_volume_m3: '',
  time_window_start: '',
  time_window_end: '',
  priority: 'standard',
  service_duration_min: '10',
};

export const EMPTY_VEHICLE: VehicleFormValues = {
  registration: '',
  capacity_weight_kg: '',
  capacity_volume_m3: '',
  depot_latitude: '',
  depot_longitude: '',
  operating_hours_start: '08:00',
  operating_hours_end: '18:00',
  driver_name: '',
};

export function validateOrder(values: OrderFormValues): Record<string, string> {
  const errors: Record<string, string> = {};

  const address = values.delivery_address.trim();
  if (!address) errors.delivery_address = 'Delivery address is required';
  else if (address.length > 500) errors.delivery_address = 'Must be 500 characters or fewer';

  if (!values.cargo_weight_kg.trim()) {
    errors.cargo_weight_kg = 'Cargo weight is required';
  } else {
    const weight = Number(values.cargo_weight_kg);
    if (!Number.isFinite(weight)) errors.cargo_weight_kg = 'Must be a number';
    else if (weight < 0.01 || weight > 99999.99)
      errors.cargo_weight_kg = 'Must be between 0.01 and 99999.99';
  }

  if (values.cargo_volume_m3.trim()) {
    const volume = Number(values.cargo_volume_m3);
    if (!Number.isFinite(volume) || volume < 0)
      errors.cargo_volume_m3 = 'Must be a number of 0 or more';
  }

  if (values.service_duration_min.trim()) {
    const duration = Number(values.service_duration_min);
    if (!Number.isInteger(duration) || duration < 0)
      errors.service_duration_min = 'Must be a whole number of minutes';
  }

  if (
    values.time_window_start &&
    values.time_window_end &&
    new Date(values.time_window_start) > new Date(values.time_window_end)
  ) {
    errors.time_window_end = 'Must be at or after the window start';
  }

  return errors;
}

export function orderPayload(values: OrderFormValues) {
  return {
    delivery_address: values.delivery_address.trim(),
    cargo_weight_kg: Number(values.cargo_weight_kg),
    cargo_volume_m3: values.cargo_volume_m3.trim() ? Number(values.cargo_volume_m3) : null,
    time_window_start: values.time_window_start
      ? new Date(values.time_window_start).toISOString()
      : null,
    time_window_end: values.time_window_end
      ? new Date(values.time_window_end).toISOString()
      : null,
    priority: values.priority,
    service_duration_min: values.service_duration_min ? Number(values.service_duration_min) : 10,
  };
}

export function validateVehicle(values: VehicleFormValues): Record<string, string> {
  const errors: Record<string, string> = {};

  const registration = values.registration.trim();
  if (!registration) errors.registration = 'Registration is required';
  else if (registration.length > 20) errors.registration = 'Must be 20 characters or fewer';

  if (!values.capacity_weight_kg.trim()) {
    errors.capacity_weight_kg = 'Weight capacity is required';
  } else {
    const capacity = Number(values.capacity_weight_kg);
    if (!Number.isFinite(capacity)) errors.capacity_weight_kg = 'Must be a number';
    else if (capacity < 0.01 || capacity > 99999.99)
      errors.capacity_weight_kg = 'Must be between 0.01 and 99999.99';
  }

  if (values.capacity_volume_m3.trim()) {
    const volume = Number(values.capacity_volume_m3);
    if (!Number.isFinite(volume) || volume <= 0)
      errors.capacity_volume_m3 = 'Must be a number greater than 0';
  }

  const latitude = Number(values.depot_latitude);
  if (!values.depot_latitude.trim() || !Number.isFinite(latitude))
    errors.depot_latitude = 'Depot latitude is required';
  else if (latitude < -90 || latitude > 90) errors.depot_latitude = 'Must be between -90 and 90';

  const longitude = Number(values.depot_longitude);
  if (!values.depot_longitude.trim() || !Number.isFinite(longitude))
    errors.depot_longitude = 'Depot longitude is required';
  else if (longitude < -180 || longitude > 180)
    errors.depot_longitude = 'Must be between -180 and 180';

  if (!values.operating_hours_start) errors.operating_hours_start = 'Shift start is required';
  if (!values.operating_hours_end) errors.operating_hours_end = 'Shift end is required';
  if (
    values.operating_hours_start &&
    values.operating_hours_end &&
    values.operating_hours_start >= values.operating_hours_end
  ) {
    errors.operating_hours_end = 'Must be later than the shift start';
  }

  return errors;
}

export function vehiclePayload(values: VehicleFormValues) {
  return {
    registration: values.registration.trim(),
    capacity_weight_kg: Number(values.capacity_weight_kg),
    capacity_volume_m3: values.capacity_volume_m3.trim()
      ? Number(values.capacity_volume_m3)
      : null,
    depot_location: {
      latitude: Number(values.depot_latitude),
      longitude: Number(values.depot_longitude),
    },
    operating_hours_start: values.operating_hours_start,
    operating_hours_end: values.operating_hours_end,
    driver_name: values.driver_name.trim() || null,
    available: true,
  };
}
