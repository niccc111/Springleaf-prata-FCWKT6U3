/** Test fixtures for routes, stops, and alerts. */

import type { Alert, Route, Stop, StopOrderSummary } from '@/types';

let counter = 0;
const id = (prefix: string) => `${prefix}-${(counter += 1).toString().padStart(4, '0')}`;

export function makeOrderSummary(overrides: Partial<StopOrderSummary> = {}): StopOrderSummary {
  return {
    order_id: id('order'),
    delivery_address: '30 Raffles Place, Singapore 048622',
    cargo_weight_kg: 50,
    cargo_volume_m3: 0.5,
    priority: 'standard',
    time_window_start: null,
    time_window_end: null,
    external_ref: null,
    ...overrides,
  };
}

export function makeStop(overrides: Partial<Stop> = {}): Stop {
  const orders = overrides.orders ?? [makeOrderSummary()];
  return {
    stop_id: id('stop'),
    route_id: 'route-0001',
    order_ids: orders.map((o) => o.order_id),
    orders,
    location: { latitude: 1.284, longitude: 103.8515 },
    address: '30 Raffles Place, Singapore 048622',
    sequence_number: 1,
    eta: '2026-06-01T09:15:00Z',
    departure: '2026-06-01T09:25:00Z',
    time_window_start: null,
    time_window_end: null,
    service_duration_min: 10,
    distance_from_previous_km: 4.2,
    travel_time_from_previous_min: 12,
    has_priority_order: false,
    total_weight_kg: orders.reduce((sum, o) => sum + o.cargo_weight_kg, 0),
    total_volume_m3: 0.5,
    late: false,
    ...overrides,
  };
}

export function makeRoute(overrides: Partial<Route> = {}): Route {
  const stops = overrides.stops ?? [makeStop()];
  const totalWeight =
    overrides.total_weight_kg ?? stops.reduce((sum, s) => sum + s.total_weight_kg, 0);
  const capacity = overrides.vehicle_capacity_weight_kg ?? 1000;
  return {
    route_id: id('route'),
    vehicle_id: id('vehicle'),
    optimisation_run_id: 'run-0001',
    driver_id: null,
    vehicle_registration: 'SG1234A',
    vehicle_capacity_weight_kg: capacity,
    vehicle_capacity_volume_m3: 12,
    depot_location: { latitude: 1.279, longitude: 103.809 },
    total_distance_km: 24.567,
    total_duration_min: 78,
    total_weight_kg: totalWeight,
    total_volume_m3: 3.2,
    weight_utilisation_pct: Math.round((totalWeight / capacity) * 1000) / 10,
    volume_utilisation_pct: 26.7,
    stop_count: stops.length,
    locked: false,
    status: 'draft',
    data_quality_warning: false,
    data_quality_message: null,
    needs_reoptimisation: false,
    priority_relaxed: false,
    travel_times_updated_at: null,
    completed_at: null,
    created_at: '2026-06-01T07:00:00Z',
    updated_at: '2026-06-01T07:00:00Z',
    ...overrides,
    stops,
  };
}

export function makeAlert(overrides: Partial<Alert> = {}): Alert {
  return {
    alert_id: id('alert'),
    alert_type: 'overload',
    severity: 'critical',
    entity_type: 'vehicle',
    entity_id: 'vehicle-0001',
    message: 'Vehicle SG1234A is at or over capacity on route route-0001',
    context: {},
    raised_at: new Date().toISOString(),
    acknowledged: false,
    acknowledged_by: null,
    acknowledged_at: null,
    ...overrides,
  };
}
