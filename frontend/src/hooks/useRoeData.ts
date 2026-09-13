/** React Query hooks for the dispatcher data set. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useAppStore } from '@/store';
import type { Route, RouteStatus } from '@/types';

export const ROUTES_KEY = ['routes'] as const;
export const ALERTS_KEY = ['alerts'] as const;
export const ORDERS_KEY = ['orders'] as const;
export const VEHICLES_KEY = ['vehicles'] as const;
export const SUMMARY_KEY = ['summary'] as const;

export function useRoutes() {
  return useQuery({
    queryKey: ROUTES_KEY,
    queryFn: () => api.listRoutes(),
    // The WebSocket drives freshness; this is the safety net if it drops.
    refetchInterval: 30_000,
    staleTime: 5_000,
  });
}

export function useAlerts() {
  const setAlerts = useAppStore((s) => s.setAlerts);
  return useQuery({
    queryKey: ALERTS_KEY,
    queryFn: async () => {
      const alerts = await api.listAlerts({ acknowledged: false });
      setAlerts(alerts);
      return alerts;
    },
    refetchInterval: 45_000,
  });
}

export function useOrders() {
  return useQuery({
    queryKey: ORDERS_KEY,
    queryFn: () => api.listOrders(),
    refetchInterval: 60_000,
  });
}

export function useVehicles() {
  return useQuery({
    queryKey: VEHICLES_KEY,
    queryFn: () => api.listVehicles(),
    refetchInterval: 120_000,
  });
}

export function useSummary() {
  const setPendingOrders = useAppStore((s) => s.setPendingOrders);
  return useQuery({
    queryKey: SUMMARY_KEY,
    queryFn: async () => {
      const summary = await api.summary();
      setPendingOrders(summary.unassigned_orders);
      return summary;
    },
    refetchInterval: 30_000,
  });
}

export function useConnectivity() {
  const setConnectivity = useAppStore((s) => s.setConnectivity);
  return useQuery({
    queryKey: ['connectivity'],
    queryFn: async () => {
      const statuses = await api.connectivity();
      setConnectivity(statuses);
      return statuses;
    },
    refetchInterval: 30_000,
  });
}

export function useRouteMutations() {
  const queryClient = useQueryClient();
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ROUTES_KEY });
    void queryClient.invalidateQueries({ queryKey: ORDERS_KEY });
    void queryClient.invalidateQueries({ queryKey: ALERTS_KEY });
    void queryClient.invalidateQueries({ queryKey: SUMMARY_KEY });
  };

  const patchRoute = useMutation({
    mutationFn: ({ routeId, patch }: { routeId: string; patch: { locked?: boolean; status?: RouteStatus } }) =>
      api.patchRoute(routeId, patch),
    onSuccess: invalidate,
  });

  const exportRoute = useMutation({
    mutationFn: (routeId: string) => api.exportRoute(routeId),
    onSuccess: invalidate,
  });

  const recalculate = useMutation({
    mutationFn: (routeId: string) => api.recalculateRoute(routeId),
    onSuccess: invalidate,
  });

  const reassign = useMutation({
    mutationFn: (payload: {
      order_id: string;
      source_route_id?: string | null;
      dest_route_id: string;
      confirm_late_delivery?: boolean;
    }) => api.reassignOrder(payload),
    onSuccess: invalidate,
  });

  const acknowledgeAlert = useMutation({
    mutationFn: (alertId: string) => api.acknowledgeAlert(alertId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ALERTS_KEY }),
  });

  const optimise = useMutation({
    mutationFn: (reoptimise: boolean) => api.optimise(reoptimise),
    onSuccess: invalidate,
  });

  return { patchRoute, exportRoute, recalculate, reassign, acknowledgeAlert, optimise, invalidate };
}

export function findRoute(routes: Route[] | undefined, routeId: string | null): Route | null {
  if (!routes || !routeId) return null;
  return routes.find((route) => route.route_id === routeId) ?? null;
}
