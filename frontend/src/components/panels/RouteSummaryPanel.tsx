/**
 * Route summary list beside the map (Requirement 9.1).
 *
 * Each entry shows the vehicle, stop count, distance to 2 d.p., duration as a
 * whole number, and weight (plus volume where defined) utilisation to 1 d.p.
 */

import {
  ArrowUpDown,
  GitCompareArrows,
  Lock,
  PackageSearch,
  TriangleAlert,
  Truck,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { LoadBar } from '@/components/panels/LoadBar';
import { Badge } from '@/components/ui/badge';
import { Select } from '@/components/ui/select';
import { EmptyState, ErrorState, SkeletonList } from '@/components/ui/states';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn, formatDistanceKm, formatDurationMin, pluralise, vehicleColour } from '@/lib/utils';
import { useAppStore } from '@/store';
import type { Route } from '@/types';

type SortKey = 'vehicle' | 'stops' | 'distance' | 'load';

export interface RouteSummaryPanelProps {
  routes: Route[];
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
}

export function RouteSummaryPanel({ routes, isLoading, isError, onRetry }: RouteSummaryPanelProps) {
  const [sortKey, setSortKey] = useState<SortKey>('vehicle');
  const selectedRouteId = useAppStore((s) => s.selectedRouteId);
  const selectRoute = useAppStore((s) => s.selectRoute);
  const changedRouteIds = useAppStore((s) => s.changedRouteIds);

  const sorted = useMemo(() => {
    const copy = [...routes];
    copy.sort((a, b) => {
      switch (sortKey) {
        case 'stops':
          return b.stop_count - a.stop_count;
        case 'distance':
          return b.total_distance_km - a.total_distance_km;
        case 'load':
          return (b.weight_utilisation_pct ?? -1) - (a.weight_utilisation_pct ?? -1);
        default:
          return (a.vehicle_registration ?? '').localeCompare(b.vehicle_registration ?? '');
      }
    });
    return copy;
  }, [routes, sortKey]);

  if (isError) {
    return (
      <ErrorState
        title="Routes could not be loaded"
        description="The route list is unavailable right now."
        onRetry={onRetry}
      />
    );
  }

  if (isLoading) return <SkeletonList rows={4} />;

  if (routes.length === 0) {
    return (
      <EmptyState
        icon={PackageSearch}
        title="No routes planned"
        description="Import or add orders and vehicles, then run optimisation to build the day's routes."
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <p className="text-xs text-muted-foreground">
          {pluralise(routes.length, 'route')} ·{' '}
          {pluralise(routes.reduce((sum, r) => sum + r.stop_count, 0), 'stop')}
        </p>
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <ArrowUpDown className="h-3 w-3" aria-hidden="true" />
          <span className="sr-only">Sort routes by</span>
          <Select
            value={sortKey}
            onChange={(event) => setSortKey(event.target.value as SortKey)}
            className="h-7 w-auto border-none bg-transparent px-1 py-0 text-xs shadow-none"
            aria-label="Sort routes by"
          >
            <option value="vehicle">Vehicle</option>
            <option value="stops">Stops</option>
            <option value="distance">Distance</option>
            <option value="load">Load</option>
          </Select>
        </label>
      </div>

      <ul className="flex-1 space-y-2 overflow-y-auto p-3">
        {sorted.map((route) => {
          const selected = route.route_id === selectedRouteId;
          const changed = changedRouteIds[route.route_id];
          return (
            <li key={route.route_id}>
              <button
                type="button"
                onClick={() => selectRoute(selected ? null : route.route_id)}
                aria-pressed={selected}
                className={cn(
                  'w-full rounded-lg border p-3 text-left transition-all hover:border-primary/50 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  selected ? 'border-primary bg-primary/5 shadow-sm' : 'border-border bg-card',
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      className="h-3 w-3 shrink-0 rounded-full ring-2 ring-background"
                      style={{ backgroundColor: vehicleColour(route.vehicle_id) }}
                      aria-hidden="true"
                    />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold">
                        {route.vehicle_registration ?? 'Unassigned vehicle'}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {pluralise(route.stop_count, 'stop')} ·{' '}
                        {formatDistanceKm(route.total_distance_km)} km ·{' '}
                        {formatDurationMin(route.total_duration_min)} min
                      </p>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {route.locked && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Lock className="h-3.5 w-3.5 text-muted-foreground" aria-label="Locked" />
                        </TooltipTrigger>
                        <TooltipContent>Locked — excluded from re-optimisation</TooltipContent>
                      </Tooltip>
                    )}
                    {route.data_quality_warning && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <TriangleAlert
                            className="h-3.5 w-3.5 text-warning"
                            aria-label="Data quality warning"
                            data-testid="indicator-data-quality-route"
                          />
                        </TooltipTrigger>
                        <TooltipContent>
                          {route.data_quality_message ?? 'Travel times are estimated'}
                        </TooltipContent>
                      </Tooltip>
                    )}
                    {changed && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <GitCompareArrows
                            className="h-3.5 w-3.5 text-primary"
                            aria-label="Changed by the last run"
                          />
                        </TooltipTrigger>
                        <TooltipContent>
                          {changed.change === 'created'
                            ? 'New in the last run'
                            : `${changed.added_order_ids.length} added, ${changed.removed_order_ids.length} removed${changed.sequence_changed ? ', resequenced' : ''}${changed.driver_changed ? ', driver changed' : ''}`}
                        </TooltipContent>
                      </Tooltip>
                    )}
                    <StatusBadge status={route.status} />
                  </div>
                </div>

                <div className="mt-3 space-y-2">
                  <LoadBar
                    label="Weight"
                    utilisation={route.weight_utilisation_pct}
                    compact
                  />
                  {route.vehicle_capacity_volume_m3 !== null && (
                    <LoadBar
                      label="Volume"
                      utilisation={route.volume_utilisation_pct}
                      compact
                    />
                  )}
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function StatusBadge({ status }: { status: Route['status'] }) {
  const variant =
    status === 'dispatched' ? 'success' : status === 'approved' ? 'default' : status === 'completed' ? 'secondary' : 'outline';
  return (
    <Badge variant={variant} className="capitalize">
      {status === 'dispatched' && <Truck className="h-3 w-3" aria-hidden="true" />}
      {status}
    </Badge>
  );
}
