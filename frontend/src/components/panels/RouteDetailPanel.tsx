/**
 * Selected-route detail with drag-and-drop reassignment (Requirements 8.4, 10.1).
 *
 * Drag is implemented with pointer/HTML5 drag events on the stop list rather
 * than on the map canvas, per the design. Dropping an order onto another route
 * calls POST /orders/reassign; capacity, lock, and late-delivery responses are
 * surfaced inline.
 */

import {
  ChevronLeft,
  Clock,
  GripVertical,
  Lock,
  LockOpen,
  MapPin,
  RefreshCw,
  Send,
  TriangleAlert,
  Truck,
} from 'lucide-react';
import { useState } from 'react';
import { LoadBar } from '@/components/panels/LoadBar';
import { StatusBadge } from '@/components/panels/RouteSummaryPanel';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import {
  cn,
  formatDistanceKm,
  formatDurationMin,
  formatTime,
  formatTimeWindow,
  formatWeight,
  pluralise,
  truncateId,
  vehicleColour,
} from '@/lib/utils';
import { useAppStore } from '@/store';
import type { Route, Stop } from '@/types';

export const DRAG_MIME = 'application/x-roe-order';

export interface DragPayload {
  orderId: string;
  sourceRouteId: string;
  address: string;
  weightKg: number;
}

export interface RouteDetailPanelProps {
  route: Route;
  onBack: () => void;
  onLockToggle: (locked: boolean) => void;
  onApprove: () => void;
  onExport: () => void;
  onRecalculate: () => void;
  onDropOrder: (payload: DragPayload, destRouteId: string) => void;
  busy?: boolean;
}

export function RouteDetailPanel({
  route,
  onBack,
  onLockToggle,
  onApprove,
  onExport,
  onRecalculate,
  onDropOrder,
  busy,
}: RouteDetailPanelProps) {
  const [dragOver, setDragOver] = useState(false);
  const selectedStopId = useAppStore((s) => s.selectedStopId);
  const selectStop = useAppStore((s) => s.selectStop);

  const editable = route.status === 'draft' || route.status === 'approved';
  const acceptsDrops = editable && !route.locked;

  const handleDrop = (event: React.DragEvent) => {
    event.preventDefault();
    setDragOver(false);
    const raw = event.dataTransfer.getData(DRAG_MIME);
    if (!raw) return;
    try {
      const payload = JSON.parse(raw) as DragPayload;
      if (payload.sourceRouteId === route.route_id) return;
      onDropOrder(payload, route.route_id);
    } catch {
      /* malformed drag payload */
    }
  };

  return (
    <div
      className={cn(
        'flex h-full flex-col transition-colors',
        dragOver && acceptsDrops && 'bg-primary/5 ring-2 ring-inset ring-primary',
        dragOver && !acceptsDrops && 'bg-destructive/5 ring-2 ring-inset ring-destructive',
      )}
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes(DRAG_MIME)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = acceptsDrops ? 'move' : 'none';
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      <header className="border-b border-border p-3">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon-sm" onClick={onBack} aria-label="Back to route list">
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span
            className="h-3 w-3 shrink-0 rounded-full"
            style={{ backgroundColor: vehicleColour(route.vehicle_id) }}
            aria-hidden="true"
          />
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">
            {route.vehicle_registration ?? 'Unassigned vehicle'}
          </h2>
          <StatusBadge status={route.status} />
        </div>

        <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
          <div>
            <dt className="text-muted-foreground">Distance</dt>
            <dd className="font-medium tabular-nums" data-testid="detail-distance">
              {formatDistanceKm(route.total_distance_km)} km
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Travel time</dt>
            <dd className="font-medium tabular-nums" data-testid="detail-duration">
              {formatDurationMin(route.total_duration_min)} min
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Load</dt>
            <dd className="font-medium tabular-nums" data-testid="detail-weight">
              {formatWeight(route.total_weight_kg)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Stops</dt>
            <dd className="font-medium tabular-nums">{route.stop_count}</dd>
          </div>
        </dl>

        <div className="mt-3 space-y-2">
          <LoadBar
            label="Weight"
            utilisation={route.weight_utilisation_pct}
            detail={
              route.vehicle_capacity_weight_kg
                ? `${formatWeight(route.total_weight_kg)} of ${formatWeight(route.vehicle_capacity_weight_kg)}`
                : undefined
            }
          />
          {route.vehicle_capacity_volume_m3 !== null && (
            <LoadBar label="Volume" utilisation={route.volume_utilisation_pct} />
          )}
        </div>

        {route.data_quality_warning && (
          <p className="mt-3 flex items-start gap-1.5 rounded-md border border-warning/40 bg-warning/10 p-2 text-[11px] leading-relaxed">
            <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0 text-warning" aria-hidden="true" />
            {route.data_quality_message ?? 'Travel times are estimated from historical averages.'}
          </p>
        )}
        {route.priority_relaxed && (
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            Priority ordering was relaxed on this route because a capacity or time-window
            constraint made it infeasible.
          </p>
        )}
        {route.needs_reoptimisation && (
          <p className="mt-2 rounded-md border border-primary/30 bg-primary/5 p-2 text-[11px] leading-relaxed">
            A new priority order affects this route — re-optimise to fold it in.
          </p>
        )}

        <div className="mt-3 flex flex-wrap gap-1.5">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onLockToggle(!route.locked)}
            disabled={busy || !editable}
            title={
              editable
                ? route.locked
                  ? 'Unlock so re-optimisation can change this route'
                  : 'Lock to protect this route from re-optimisation'
                : `A ${route.status} route cannot be unlocked`
            }
          >
            {route.locked ? <LockOpen className="h-3.5 w-3.5" /> : <Lock className="h-3.5 w-3.5" />}
            {route.locked ? 'Unlock' : 'Lock'}
          </Button>

          {route.status === 'draft' && (
            <Button variant="default" size="sm" onClick={onApprove} disabled={busy}>
              <Truck className="h-3.5 w-3.5" />
              Approve &amp; dispatch
            </Button>
          )}
          {route.status === 'approved' && (
            <Button variant="default" size="sm" onClick={onExport} disabled={busy}>
              <Send className="h-3.5 w-3.5" />
              Re-send to platform
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={onRecalculate}
            disabled={busy || !editable}
            title="Refresh ETAs using current traffic"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh ETAs
          </Button>
        </div>
      </header>

      <div className="border-b border-border px-3 py-1.5 text-[11px] text-muted-foreground">
        {acceptsDrops
          ? 'Drag a stop onto another route in the list to reassign it.'
          : route.locked
            ? 'This route is locked — unlock it to make changes.'
            : `A ${route.status} route can no longer be changed.`}
      </div>

      <ol className="flex-1 space-y-1.5 overflow-y-auto p-3">
        {route.stops.length === 0 && (
          <li className="rounded-md border border-dashed border-border p-4 text-center text-xs text-muted-foreground">
            This route has no stops.
          </li>
        )}
        {[...route.stops]
          .sort((a, b) => a.sequence_number - b.sequence_number)
          .map((stop) => (
            <StopRow
              key={stop.stop_id}
              stop={stop}
              route={route}
              selected={stop.stop_id === selectedStopId}
              draggable={acceptsDrops}
              onSelect={() => selectStop(stop.stop_id === selectedStopId ? null : stop.stop_id, route.route_id)}
            />
          ))}
      </ol>
    </div>
  );
}

function StopRow({
  stop,
  route,
  selected,
  draggable,
  onSelect,
}: {
  stop: Stop;
  route: Route;
  selected: boolean;
  draggable: boolean;
  onSelect: () => void;
}) {
  const [dragging, setDragging] = useState(false);
  const primaryOrder = stop.orders[0];

  return (
    <li>
      <div
        role="button"
        tabIndex={0}
        aria-pressed={selected}
        draggable={draggable}
        onDragStart={(event) => {
          if (!primaryOrder) return;
          const payload: DragPayload = {
            orderId: primaryOrder.order_id,
            sourceRouteId: route.route_id,
            address: stop.address,
            weightKg: stop.total_weight_kg,
          };
          event.dataTransfer.setData(DRAG_MIME, JSON.stringify(payload));
          event.dataTransfer.effectAllowed = 'move';
          setDragging(true);
        }}
        onDragEnd={() => setDragging(false)}
        onClick={onSelect}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onSelect();
          }
        }}
        className={cn(
          'flex w-full cursor-pointer items-start gap-2 rounded-md border p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          selected ? 'border-primary bg-primary/5' : 'border-border bg-card hover:bg-accent/40',
          dragging && 'opacity-50',
          stop.late && 'border-destructive/50',
        )}
      >
        {draggable && (
          <GripVertical
            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground"
            aria-hidden="true"
          />
        )}
        <span
          className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold text-white"
          style={{ backgroundColor: vehicleColour(route.vehicle_id) }}
        >
          {stop.sequence_number}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium">{stop.address}</p>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
            <span className={cn('inline-flex items-center gap-1', stop.late && 'text-destructive font-medium')}>
              <Clock className="h-3 w-3" aria-hidden="true" />
              {formatTime(stop.eta)}
            </span>
            <span>{formatWeight(stop.total_weight_kg)}</span>
            <span>{pluralise(stop.order_ids.length, 'order')}</span>
          </div>
          {(stop.time_window_start || stop.time_window_end) && (
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              Window {formatTimeWindow(stop.time_window_start, stop.time_window_end)}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {stop.has_priority_order && (
            <Badge variant="warning" className="px-1.5 py-0 text-[10px]">
              Priority
            </Badge>
          )}
          {stop.late && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="destructive" className="px-1.5 py-0 text-[10px]">
                  Late
                </Badge>
              </TooltipTrigger>
              <TooltipContent>
                ETA {formatTime(stop.eta)} is after the window closes at{' '}
                {formatTime(stop.time_window_end)}
              </TooltipContent>
            </Tooltip>
          )}
        </div>
      </div>
    </li>
  );
}

export function StopDetailPanel({ stop, route, onClose }: { stop: Stop; route: Route; onClose: () => void }) {
  return (
    <div className="max-h-[45%] shrink-0 overflow-y-auto border-t border-border bg-card p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <MapPin className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <h3 className="text-sm font-semibold">Stop {stop.sequence_number}</h3>
          {stop.late && <Badge variant="destructive">Late</Badge>}
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close stop details">
          <ChevronLeft className="h-4 w-4 rotate-90" />
        </Button>
      </div>

      <p className="mt-1 text-xs text-muted-foreground">{stop.address}</p>

      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
        <div>
          <dt className="text-muted-foreground">ETA</dt>
          <dd className={cn('font-medium tabular-nums', stop.late && 'text-destructive')}>
            {formatTime(stop.eta)}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Time window</dt>
          <dd className="font-medium">
            {formatTimeWindow(stop.time_window_start, stop.time_window_end)}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Cargo at stop</dt>
          <dd className="font-medium tabular-nums">{formatWeight(stop.total_weight_kg)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Service time</dt>
          <dd className="font-medium tabular-nums">{stop.service_duration_min} min</dd>
        </div>
      </dl>

      <div className="mt-3">
        <p className="text-xs font-medium">Orders at this stop</p>
        <ul className="mt-1.5 space-y-1">
          {stop.orders.map((order) => (
            <li
              key={order.order_id}
              className="flex items-center justify-between gap-2 rounded border border-border px-2 py-1 text-[11px]"
            >
              <span className="truncate font-mono text-muted-foreground" title={order.order_id}>
                {order.external_ref ?? truncateId(order.order_id)}
              </span>
              <span className="flex shrink-0 items-center gap-1.5">
                {order.priority === 'priority' && (
                  <Badge variant="warning" className="px-1 py-0 text-[10px]">
                    Priority
                  </Badge>
                )}
                <span className="tabular-nums">{formatWeight(order.cargo_weight_kg)}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>

      <p className="mt-2 text-[11px] text-muted-foreground">
        On {route.vehicle_registration ?? 'unassigned vehicle'}
      </p>
    </div>
  );
}
