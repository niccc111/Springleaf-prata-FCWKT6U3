/** Typed REST client for the ROE API gateway. */

import type {
  Alert,
  ApiErrorBody,
  AuditEntry,
  ExportJob,
  IntegrationStatus,
  Order,
  OptimisationRun,
  Page,
  ReassignResponse,
  Route,
  RouteStatus,
  UploadResult,
  Vehicle,
} from '@/types';

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
export const API_PREFIX = `${BASE}/api/v1`;

export class ApiError extends Error {
  readonly status: number;
  readonly body: ApiErrorBody;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || `Request failed with status ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }

  /** Field-level errors keyed by field name, for form restoration. */
  get fieldErrors(): Record<string, string> {
    const map: Record<string, string> = {};
    for (const entry of this.body.fields ?? []) map[entry.field] = entry.message;
    return map;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData) && init.body !== undefined) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(`${API_PREFIX}${path}`, { ...init, headers });

  if (!response.ok) {
    let body: ApiErrorBody;
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      body = { error: 'network_error', message: response.statusText || 'Request failed' };
    }
    throw new ApiError(response.status, body);
  }

  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('application/json')) return (await response.blob()) as unknown as T;
  return (await response.json()) as T;
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

export const api = {
  // ---- routes ----
  listRoutes: (params?: { status?: RouteStatus; includeCompleted?: boolean }) => {
    const search = new URLSearchParams();
    if (params?.status) search.set('status', params.status);
    if (params?.includeCompleted === false) search.set('include_completed', 'false');
    const qs = search.toString();
    return request<Route[]>(`/routes${qs ? `?${qs}` : ''}`);
  },
  getRoute: (routeId: string) => request<Route>(`/routes/${routeId}`),
  patchRoute: (routeId: string, patch: { locked?: boolean; status?: RouteStatus }) =>
    request<Route>(`/routes/${routeId}`, { method: 'PATCH', ...json(patch) }),
  exportRoute: (routeId: string) =>
    request<ExportJob>(`/routes/${routeId}/export`, { method: 'POST' }),
  listExports: (routeId: string) => request<ExportJob[]>(`/routes/${routeId}/exports`),
  recalculateRoute: (routeId: string) =>
    request<Route>(`/routes/${routeId}/recalculate`, { method: 'POST' }),
  reassignOrder: (payload: {
    order_id: string;
    source_route_id?: string | null;
    dest_route_id: string;
    position?: number;
    confirm_late_delivery?: boolean;
  }) => request<ReassignResponse>('/orders/reassign', { method: 'POST', ...json(payload) }),

  // ---- orders ----
  listOrders: (params?: { status?: string; needsReview?: boolean; limit?: number }) => {
    const search = new URLSearchParams();
    if (params?.status) search.set('status', params.status);
    if (params?.needsReview !== undefined) search.set('needs_review', String(params.needsReview));
    search.set('limit', String(params?.limit ?? 300));
    return request<Page<Order>>(`/orders?${search.toString()}`);
  },
  createOrder: (payload: Record<string, unknown>) =>
    request<Order>('/orders', { method: 'POST', ...json(payload) }),
  updateOrder: (orderId: string, payload: Record<string, unknown>) =>
    request<Order>(`/orders/${orderId}`, { method: 'PATCH', ...json(payload) }),
  setOrderCoordinates: (orderId: string, latitude: number, longitude: number) =>
    request<Order>(`/orders/${orderId}/coordinates`, {
      method: 'POST',
      ...json({ latitude, longitude }),
    }),

  // ---- vehicles ----
  listVehicles: (params?: { available?: boolean }) => {
    const search = new URLSearchParams({ limit: '300' });
    if (params?.available !== undefined) search.set('available', String(params.available));
    return request<Page<Vehicle>>(`/vehicles?${search.toString()}`);
  },
  createVehicle: (payload: Record<string, unknown>) =>
    request<Vehicle>('/vehicles', { method: 'POST', ...json(payload) }),
  updateVehicle: (vehicleId: string, payload: Record<string, unknown>) =>
    request<Vehicle>(`/vehicles/${vehicleId}`, { method: 'PATCH', ...json(payload) }),

  // ---- optimisation ----
  optimise: (reoptimise: boolean) =>
    request<OptimisationRun>('/optimise', { method: 'POST', ...json({ reoptimise }) }),
  currentRun: () => request<OptimisationRun | null>('/optimise/current'),
  listRuns: () => request<OptimisationRun[]>('/optimise/runs'),

  // ---- alerts ----
  listAlerts: (params?: { acknowledged?: boolean; limit?: number }) => {
    const search = new URLSearchParams({ limit: String(params?.limit ?? 200) });
    if (params?.acknowledged !== undefined) {
      search.set('acknowledged', String(params.acknowledged));
    }
    return request<Alert[]>(`/alerts?${search.toString()}`);
  },
  acknowledgeAlert: (alertId: string) =>
    request<Alert>(`/alerts/${alertId}/acknowledge`, { method: 'PATCH' }),

  // ---- upload ----
  uploadOrders: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<UploadResult>('/upload/orders', { method: 'POST', body: form });
  },
  uploadVehicles: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<UploadResult>('/upload/vehicles', { method: 'POST', body: form });
  },
  templateUrl: (kind: 'orders' | 'vehicles', format: 'csv' | 'xlsx') =>
    `${API_PREFIX}/upload/templates/${kind}?format=${format}`,
  downloadTemplate: async (kind: 'orders' | 'vehicles', format: 'csv' | 'xlsx') => {
    const blob = await request<Blob>(`/upload/templates/${kind}?format=${format}`);
    return blob;
  },

  // ---- audit ----
  listAudit: (params: Record<string, string | undefined>) => {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value) search.set(key, value);
    }
    return request<Page<AuditEntry>>(`/audit?${search.toString()}`);
  },

  // ---- system ----
  connectivity: () => request<IntegrationStatus[]>('/system/connectivity'),
  adapterConfig: () => request<Record<string, string>>('/system/config'),
  summary: () => request<{ unassigned_orders: number; active_run: boolean }>('/system/summary'),
};
