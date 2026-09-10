/**
 * Connectivity banner (Requirements 1.5, 2.6, 7.4).
 *
 * Stays visible while any integration is degraded and clears itself when the
 * `connectivity.restored` event arrives.
 */

import { PlugZap, WifiOff } from 'lucide-react';
import { useMemo } from 'react';
import { degradedSystems, selectConnectivity, useAppStore } from '@/store';

const NAMES: Record<string, string> = {
  oms: 'Order Management System',
  fms: 'Fleet Management System',
  mapping: 'Mapping Service',
  delivery_platform: 'Delivery Platform',
};

const CONSEQUENCE: Record<string, string> = {
  oms: 'New orders are not arriving automatically — use manual entry or spreadsheet upload.',
  fms: 'Working from the last known vehicle records.',
  mapping: 'Travel times are estimated from historical averages, so ETAs may drift.',
  delivery_platform: 'Approved routes cannot be dispatched until the platform is reachable.',
};

export function ConnectivityBanner() {
  const connectivity = useAppStore(selectConnectivity);
  const socketConnected = useAppStore((s) => s.socketConnected);
  const degraded = useMemo(() => degradedSystems(connectivity), [connectivity]);

  if (degraded.length === 0 && socketConnected) return null;

  return (
    <div role="status" aria-live="polite" className="space-y-px">
      {!socketConnected && (
        <div className="flex items-center gap-2 bg-muted px-4 py-1.5 text-xs text-muted-foreground">
          <WifiOff className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>
            Live updates are disconnected — reconnecting. Route data still refreshes periodically.
          </span>
        </div>
      )}
      {degraded.map((status) => (
        <div
          key={status.system}
          className="flex items-start gap-2 bg-warning/15 px-4 py-1.5 text-xs text-warning-foreground"
        >
          <PlugZap className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" aria-hidden="true" />
          <span className="text-foreground">
            <strong className="font-semibold">
              {NAMES[status.system] ?? status.system} unavailable.
            </strong>{' '}
            {CONSEQUENCE[status.system] ?? status.message ?? ''}
          </span>
        </div>
      ))}
    </div>
  );
}
