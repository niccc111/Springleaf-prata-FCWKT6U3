/**
 * Persistent alert panel (Requirement 14.4).
 *
 * Shows every unacknowledged alert with its type, severity, referenced entity
 * ids, and creation time. Acknowledgement is first-writer-wins; a 409 is shown
 * as "already acknowledged" rather than an error.
 */

import {
  BellOff,
  CheckCheck,
  CircleAlert,
  Clock,
  PackageX,
  SendHorizonal,
  Weight,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState, SkeletonList } from '@/components/ui/states';
import { cn, formatDateTime, relativeTime, truncateId } from '@/lib/utils';
import type { Alert, AlertType } from '@/types';

const ALERT_ICONS: Record<AlertType, LucideIcon> = {
  overload: Weight,
  late_delivery: Clock,
  impossible_order: PackageX,
  export_failure: SendHorizonal,
};

const ALERT_LABELS: Record<AlertType, string> = {
  overload: 'Vehicle overloaded',
  late_delivery: 'Late delivery',
  impossible_order: 'Order cannot be planned',
  export_failure: 'Export failed',
};

export interface AlertPanelProps {
  alerts: Alert[];
  isLoading: boolean;
  onAcknowledge: (alertId: string) => void;
  acknowledgingId?: string | null;
  onSelectEntity?: (alert: Alert) => void;
}

export function AlertPanel({
  alerts,
  isLoading,
  onAcknowledge,
  acknowledgingId,
  onSelectEntity,
}: AlertPanelProps) {
  if (isLoading) return <SkeletonList rows={3} />;

  if (alerts.length === 0) {
    return (
      <EmptyState
        icon={BellOff}
        title="No open alerts"
        description="Overloads, late deliveries, and unplaceable orders appear here as soon as they are detected."
      />
    );
  }

  return (
    <ul className="space-y-2 p-3" aria-label="Unacknowledged alerts">
      {alerts.map((alert) => {
        const Icon = ALERT_ICONS[alert.alert_type] ?? CircleAlert;
        const critical = alert.severity === 'critical';
        return (
          <li
            key={alert.alert_id}
            className={cn(
              'rounded-lg border p-3',
              critical ? 'border-destructive/40 bg-destructive/5' : 'border-warning/40 bg-warning/5',
            )}
          >
            <div className="flex items-start gap-2">
              <Icon
                className={cn('mt-0.5 h-4 w-4 shrink-0', critical ? 'text-destructive' : 'text-warning')}
                aria-hidden="true"
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs font-semibold">{ALERT_LABELS[alert.alert_type]}</span>
                  <Badge variant={critical ? 'destructive' : 'warning'} className="px-1.5 py-0 text-[10px]">
                    {alert.severity}
                  </Badge>
                </div>
                <p className="mt-1 break-words text-[11px] leading-relaxed text-muted-foreground">
                  {alert.message}
                </p>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground">
                  <span className="font-mono" title={alert.entity_id}>
                    {alert.entity_type}: {truncateId(alert.entity_id)}
                  </span>
                  <span title={formatDateTime(alert.raised_at)}>{relativeTime(alert.raised_at)}</span>
                </div>
              </div>
            </div>
            <div className="mt-2 flex justify-end gap-1.5">
              {onSelectEntity && typeof alert.context?.route_id === 'string' && (
                <Button variant="ghost" size="sm" onClick={() => onSelectEntity(alert)}>
                  Show route
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => onAcknowledge(alert.alert_id)}
                disabled={acknowledgingId === alert.alert_id}
              >
                <CheckCheck className="h-3.5 w-3.5" />
                Acknowledge
              </Button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
