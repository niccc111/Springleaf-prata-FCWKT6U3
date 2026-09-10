/**
 * Drop targets for reassigning a stop to another route (Requirement 10.1).
 *
 * Opening a route replaces the route list with its detail, so without this the
 * dispatcher would have nowhere to drop. Each entry shows the receiving
 * vehicle's remaining headroom, and a route that cannot take the load — locked,
 * dispatched, or already full — says so instead of silently rejecting the drop.
 */

import { Lock, MoveRight } from 'lucide-react';
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { cn, formatWeight, vehicleColour } from '@/lib/utils';
import type { Route } from '@/types';
import type { DragPayload } from '@/components/panels/RouteDetailPanel';
import { DRAG_MIME } from '@/components/panels/RouteDetailPanel';

export interface ReassignTargetsProps {
  routes: Route[];
  currentRouteId: string;
  onDropOrder: (payload: DragPayload, destRouteId: string) => void;
}

function headroomKg(route: Route): number | null {
  if (route.vehicle_capacity_weight_kg === null) return null;
  return route.vehicle_capacity_weight_kg - route.total_weight_kg;
}

function blockedReason(route: Route): string | null {
  // A dispatched route is always locked, so report the status first — it is
  // the more informative of the two reasons.
  if (route.status === 'dispatched' || route.status === 'completed') return route.status;
  if (route.locked) return 'Locked';
  return null;
}

export function ReassignTargets({ routes, currentRouteId, onDropOrder }: ReassignTargetsProps) {
  // Most headroom first, and routes that cannot accept a drop last, so the
  // dispatcher's viable options are the ones in reach.
  const targets = routes
    .filter((route) => route.route_id !== currentRouteId)
    .sort((a, b) => {
      const blockedDelta = Number(Boolean(blockedReason(a))) - Number(Boolean(blockedReason(b)));
      if (blockedDelta !== 0) return blockedDelta;
      return (headroomKg(b) ?? -1) - (headroomKg(a) ?? -1);
    });
  if (targets.length === 0) return null;

  return (
    <section className="shrink-0 border-t border-border bg-muted/30 p-3">
      <h3 className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <MoveRight className="h-3.5 w-3.5" aria-hidden="true" />
        Drop a stop here to move it
      </h3>
      <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto">
        {targets.map((route) => (
          <TargetRow key={route.route_id} route={route} onDropOrder={onDropOrder} />
        ))}
      </ul>
    </section>
  );
}

function TargetRow({
  route,
  onDropOrder,
}: {
  route: Route;
  onDropOrder: (payload: DragPayload, destRouteId: string) => void;
}) {
  const [over, setOver] = useState(false);
  const blocked = blockedReason(route);
  const headroom = headroomKg(route);

  return (
    <li>
      <div
        onDragOver={(event) => {
          if (!event.dataTransfer.types.includes(DRAG_MIME)) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = blocked ? 'none' : 'move';
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          const raw = event.dataTransfer.getData(DRAG_MIME);
          if (!raw) return;
          try {
            onDropOrder(JSON.parse(raw) as DragPayload, route.route_id);
          } catch {
            /* malformed drag payload */
          }
        }}
        data-testid={`reassign-target-${route.route_id}`}
        className={cn(
          'flex items-center gap-2 rounded-md border border-dashed px-2 py-1.5 text-xs transition-colors',
          over && !blocked && 'border-primary bg-primary/10',
          over && blocked && 'border-destructive bg-destructive/10',
          !over && 'border-border bg-card',
        )}
      >
        <span
          className="h-2.5 w-2.5 shrink-0 rounded-full"
          style={{ backgroundColor: vehicleColour(route.vehicle_id) }}
          aria-hidden="true"
        />
        <span className="min-w-0 flex-1 truncate font-medium">
          {route.vehicle_registration ?? 'Unassigned vehicle'}
        </span>
        {blocked ? (
          <Badge variant="outline" className="shrink-0 capitalize">
            {blocked === 'Locked' && <Lock className="h-3 w-3" aria-hidden="true" />}
            {blocked}
          </Badge>
        ) : (
          <span className="shrink-0 tabular-nums text-muted-foreground">
            {headroom === null ? 'capacity unknown' : `${formatWeight(headroom)} free`}
          </span>
        )}
      </div>
    </li>
  );
}
