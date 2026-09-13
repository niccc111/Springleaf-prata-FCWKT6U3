/** Shared entity types mirroring the ROE backend schemas. */

export type OrderSource = 'OMS' | 'manual' | 'spreadsheet';
export type VehicleSource = 'FMS' | 'manual' | 'spreadsheet';
export type OrderStatus = 'unassigned' | 'assigned' | 'in_transit' | 'delivered' | 'failed';
export type Priority = 'standard' | 'priority';
export type RouteStatus = 'draft' | 'approved' | 'dispatched' | 'completed';
export type AlertType = 'overload' | 'late_delivery' | 'impossible_order' | 'export_failure';
export type AlertSeverity = 'warning' | 'critical';
export type AlertEntityType = 'vehicle' | 'order' | 'route';
export type EntityType = 'order' | 'vehicle' | 'route' | 'alert';
export type OptimisationRunStatus = 'in_progress' | 'completed' | 'timed_out' | 'aborted';
export type ExportStatus = 'pending' | 'in_progress' | 'succeeded' | 'failed' | 'cancelled';

export interface GeoPoint {
  latitude: number;
  longitude: number;
}

export interface Order {
  order_id: string;
  source: OrderSource;
  pickup_location: GeoPoint | null;
  delivery_location: GeoPoint | null;
  delivery_address: string;
  cargo_weight_kg: number;
  cargo_volume_m3: number | null;
  time_window_start: string | null;
  time_window_end: string | null;
  priority: Priority;
  status: OrderStatus;
  service_duration_min: number;
  geocode_review: boolean;
  geocode_confidence: number | null;
  created_at: string;
  updated_at: string;
  external_ref: string | null;
  route_id: string | null;
}

export interface Vehicle {
  vehicle_id: string;
  source: VehicleSource;
  registration: string;
  capacity_weight_kg: number;
  capacity_volume_m3: number | null;
  depot_location: GeoPoint;
  operating_hours_start: string;
  operating_hours_end: string;
  available: boolean;
  driver_id: string | null;
  driver_name: string | null;
  external_ref: string | null;
  created_at: string;
  updated_at: string;
}

export interface StopOrderSummary {
  order_id: string;
  delivery_address: string;
  cargo_weight_kg: number;
  cargo_volume_m3: number | null;
  priority: Priority;
  time_window_start: string | null;
  time_window_end: string | null;
  external_ref: string | null;
}

export interface Stop {
  stop_id: string;
  route_id: string;
  order_ids: string[];
  orders: StopOrderSummary[];
  location: GeoPoint;
  address: string;
  sequence_number: number;
  eta: string;
  departure: string | null;
  time_window_start: string | null;
  time_window_end: string | null;
  service_duration_min: number;
  distance_from_previous_km: number;
  travel_time_from_previous_min: number;
  has_priority_order: boolean;
  total_weight_kg: number;
  total_volume_m3: number | null;
  late: boolean;
}

export interface Route {
  route_id: string;
  vehicle_id: string;
  optimisation_run_id: string;
  driver_id: string | null;
  vehicle_registration: string | null;
  vehicle_capacity_weight_kg: number | null;
  vehicle_capacity_volume_m3: number | null;
  depot_location: GeoPoint | null;
  total_distance_km: number;
  total_duration_min: number;
  total_weight_kg: number;
  total_volume_m3: number | null;
  weight_utilisation_pct: number | null;
  volume_utilisation_pct: number | null;
  stop_count: number;
  locked: boolean;
  status: RouteStatus;
  data_quality_warning: boolean;
  data_quality_message: string | null;
  needs_reoptimisation: boolean;
  priority_relaxed: boolean;
  travel_times_updated_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  stops: Stop[];
}

export interface Alert {
  alert_id: string;
  alert_type: AlertType;
  severity: AlertSeverity;
  entity_type: AlertEntityType;
  entity_id: string;
  message: string;
  context: Record<string, unknown> | null;
  raised_at: string;
  acknowledged: boolean;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
}

export interface RouteDiffEntry {
  route_id: string;
  vehicle_id: string;
  change: 'created' | 'updated' | 'removed';
  added_order_ids: string[];
  removed_order_ids: string[];
  sequence_changed: boolean;
  driver_changed: boolean;
  previous_driver_id?: string | null;
  new_driver_id?: string | null;
}

export interface OptimisationRun {
  run_id: string;
  status: OptimisationRunStatus;
  run_type: 'initial' | 'reoptimise';
  started_at: string;
  ended_at: string | null;
  locked_excluded_count: number | null;
  orders_considered: number | null;
  orders_assigned: number | null;
  orders_unassigned: number | null;
  routes_created: number | null;
  progress_pct: number;
  progress_message: string | null;
  error_message: string | null;
  solver_status: string | null;
  duration_seconds: number | null;
  diff: { routes: RouteDiffEntry[]; changed_count: number } | null;
  initiated_by: string;
}

export interface AuditEntry {
  log_id: string;
  entity_id: string;
  entity_type: EntityType;
  old_state: Record<string, unknown> | null;
  new_state: Record<string, unknown>;
  action: string;
  acting_user: string;
  created_at: string;
}

export interface RowError {
  row_number: number;
  column: string;
  reason: string;
}

export interface UploadResult {
  imported: number;
  skipped: number;
  errors: RowError[];
  message: string;
}

export interface ExportJob {
  job_id: string;
  route_id: string;
  status: ExportStatus;
  attempts: number;
  attempt_log: Array<Record<string, unknown>> | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface IntegrationStatus {
  system: string;
  healthy: boolean;
  message: string | null;
  last_success_at: string | null;
  degraded_since: string | null;
  updated_at: string;
}

export interface FieldError {
  field: string;
  message: string;
}

export interface ApiErrorBody {
  error: string;
  message: string;
  fields?: FieldError[];
  details?: Record<string, unknown>;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ReassignResponse {
  source_route: Route | null;
  dest_route: Route;
  late_stops: string[];
  message: string;
}

/** Server → client WebSocket events (design § WebSocket events). */
export type WsEvent =
  | { event: 'connected'; payload: { user_id: string; role: string } }
  | { event: 'ping'; payload: Record<string, never> }
  | { event: 'route.updated'; payload: { route_id: string; changes: string; route: Route } }
  | { event: 'alert.raised'; payload: { alert: Alert } }
  | { event: 'alert.acknowledged'; payload: { alert_id: string; acknowledged_by: string } }
  | { event: 'optimisation.progress'; payload: { run_id: string; progress_pct: number; message: string } }
  | {
      event: 'optimisation.complete';
      payload: {
        run_id: string;
        route_ids: string[];
        locked_excluded_count: number;
        orders_assigned: number;
        orders_unassigned: number;
        diff: OptimisationRun['diff'];
      };
    }
  | { event: 'optimisation.failed'; payload: { run_id: string; reason: string } }
  | { event: 'connectivity.warning'; payload: { system: string; name: string; message: string } }
  | { event: 'connectivity.restored'; payload: { system: string; name: string } }
  | { event: 'orders.pending'; payload: { pending_count: number; message: string } }
  | { event: 'reoptimisation.suggested'; payload: { reason: string; order_id: string; route_ids: string[]; message: string } }
  | { event: 'fms.notification'; payload: { message: string; external_ref: string | null } };
