/** Zustand store: selection, live alerts, connectivity, and run state. */

import { create } from 'zustand';
import type { Alert, IntegrationStatus, OptimisationRun, RouteDiffEntry } from '@/types';

export interface Toast {
  id: string;
  title: string;
  description?: string;
  variant: 'default' | 'success' | 'warning' | 'destructive';
  durationMs?: number;
}

export interface RunProgress {
  runId: string;
  pct: number;
  message: string;
}

interface AppState {
  // selection
  selectedRouteId: string | null;
  selectedStopId: string | null;
  hoveredRouteId: string | null;
  selectRoute: (routeId: string | null) => void;
  selectStop: (stopId: string | null, routeId?: string | null) => void;
  setHoveredRoute: (routeId: string | null) => void;

  // live alerts
  alerts: Alert[];
  setAlerts: (alerts: Alert[]) => void;
  upsertAlert: (alert: Alert) => void;
  markAlertAcknowledged: (alertId: string, acknowledgedBy: string) => void;

  // optimisation
  runProgress: RunProgress | null;
  setRunProgress: (progress: RunProgress | null) => void;
  lastRun: OptimisationRun | null;
  setLastRun: (run: OptimisationRun | null) => void;
  changedRouteIds: Record<string, RouteDiffEntry>;
  setDiff: (entries: RouteDiffEntry[] | null) => void;
  pendingOrders: number;
  setPendingOrders: (count: number) => void;
  reoptimiseHint: string | null;
  setReoptimiseHint: (hint: string | null) => void;

  // connectivity
  connectivity: Record<string, IntegrationStatus>;
  setConnectivity: (statuses: IntegrationStatus[]) => void;
  degradeSystem: (system: string, name: string, message: string) => void;
  restoreSystem: (system: string) => void;

  // websocket
  socketConnected: boolean;
  setSocketConnected: (connected: boolean) => void;

  // toasts
  toasts: Toast[];
  pushToast: (toast: Omit<Toast, 'id'>) => string;
  dismissToast: (id: string) => void;

  // ui
  mapReady: boolean;
  setMapReady: (ready: boolean) => void;
}

export const useAppStore = create<AppState>((set) => ({
  selectedRouteId: null,
  selectedStopId: null,
  hoveredRouteId: null,
  selectRoute: (routeId) => set({ selectedRouteId: routeId, selectedStopId: null }),
  selectStop: (stopId, routeId) =>
    set((state) => ({
      selectedStopId: stopId,
      selectedRouteId: routeId ?? state.selectedRouteId,
    })),
  setHoveredRoute: (routeId) => set({ hoveredRouteId: routeId }),

  alerts: [],
  setAlerts: (alerts) => set({ alerts }),
  upsertAlert: (alert) =>
    set((state) => {
      const rest = state.alerts.filter((a) => a.alert_id !== alert.alert_id);
      return { alerts: [alert, ...rest] };
    }),
  markAlertAcknowledged: (alertId, acknowledgedBy) =>
    set((state) => ({
      alerts: state.alerts.map((a) =>
        a.alert_id === alertId
          ? { ...a, acknowledged: true, acknowledged_by: acknowledgedBy }
          : a,
      ),
    })),

  runProgress: null,
  setRunProgress: (progress) => set({ runProgress: progress }),
  lastRun: null,
  setLastRun: (run) => set({ lastRun: run }),
  changedRouteIds: {},
  setDiff: (entries) =>
    set({
      changedRouteIds: Object.fromEntries((entries ?? []).map((e) => [e.route_id, e])),
    }),
  pendingOrders: 0,
  setPendingOrders: (count) => set({ pendingOrders: count }),
  reoptimiseHint: null,
  setReoptimiseHint: (hint) => set({ reoptimiseHint: hint }),

  connectivity: {},
  setConnectivity: (statuses) =>
    set({ connectivity: Object.fromEntries(statuses.map((s) => [s.system, s])) }),
  degradeSystem: (system, name, message) =>
    set((state) => ({
      connectivity: {
        ...state.connectivity,
        [system]: {
          system,
          healthy: false,
          message: `${name}: ${message}`,
          last_success_at: state.connectivity[system]?.last_success_at ?? null,
          degraded_since: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      },
    })),
  restoreSystem: (system) =>
    set((state) => ({
      connectivity: {
        ...state.connectivity,
        [system]: {
          system,
          healthy: true,
          message: null,
          last_success_at: new Date().toISOString(),
          degraded_since: null,
          updated_at: new Date().toISOString(),
        },
      },
    })),

  socketConnected: false,
  setSocketConnected: (connected) => set({ socketConnected: connected }),

  toasts: [],
  pushToast: (toast) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    set((state) => ({ toasts: [...state.toasts, { ...toast, id }] }));
    return id;
  },
  dismissToast: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),

  mapReady: false,
  setMapReady: (ready) => set({ mapReady: ready }),
}));

/**
 * Derived selectors.
 *
 * These take the raw slice and are filtered in a `useMemo` by the consumer —
 * a selector that builds a new array on every call would fail Zustand's
 * `Object.is` equality check and re-render forever.
 */
export const selectAlerts = (state: AppState) => state.alerts;
export const selectConnectivity = (state: AppState) => state.connectivity;

export function unacknowledgedAlerts(alerts: Alert[]): Alert[] {
  return alerts.filter((alert) => !alert.acknowledged);
}

export function degradedSystems(
  connectivity: Record<string, IntegrationStatus>,
): IntegrationStatus[] {
  return Object.values(connectivity).filter((status) => !status.healthy);
}
