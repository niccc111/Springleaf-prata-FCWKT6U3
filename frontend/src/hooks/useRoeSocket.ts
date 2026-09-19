/**
 * Bridges WebSocket events into React Query and the Zustand store so the map,
 * route list, and alert panel refresh within 5 s of any change (Requirement 8.6).
 */

import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { roeSocket } from '@/lib/socket';
import { useAppStore } from '@/store';
import type { WsEvent } from '@/types';

export function useRoeSocket(token: string | null, enabled = true) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!enabled) return;

    const store = useAppStore.getState;

    roeSocket.onStateChange = (connected) => store().setSocketConnected(connected);
    roeSocket.connect(token ?? '');

    const unsubscribe = roeSocket.subscribe((message: WsEvent) => {
      const state = store();
      switch (message.event) {
        case 'route.updated':
          void queryClient.invalidateQueries({ queryKey: ['routes'] });
          void queryClient.invalidateQueries({ queryKey: ['orders'] });
          break;

        case 'alert.raised': {
          state.upsertAlert(message.payload.alert);
          const alert = message.payload.alert;
          state.pushToast({
            title:
              alert.alert_type === 'overload'
                ? 'Vehicle over capacity'
                : alert.alert_type === 'late_delivery'
                  ? 'Late delivery predicted'
                  : alert.alert_type === 'export_failure'
                    ? 'Export failed'
                    : 'Order cannot be planned',
            description: alert.message,
            variant: alert.severity === 'critical' ? 'destructive' : 'warning',
          });
          break;
        }

        case 'alert.acknowledged':
          state.markAlertAcknowledged(
            message.payload.alert_id,
            message.payload.acknowledged_by,
          );
          void queryClient.invalidateQueries({ queryKey: ['alerts'] });
          break;

        case 'optimisation.progress':
          state.setRunProgress({
            runId: message.payload.run_id,
            pct: message.payload.progress_pct,
            message: message.payload.message,
          });
          break;

        case 'optimisation.complete':
          state.setRunProgress(null);
          state.setDiff(message.payload.diff?.routes ?? null);
          void queryClient.invalidateQueries({ queryKey: ['routes'] });
          void queryClient.invalidateQueries({ queryKey: ['orders'] });
          void queryClient.invalidateQueries({ queryKey: ['alerts'] });
          break;

        case 'optimisation.failed':
          state.setRunProgress(null);
          state.pushToast({
            title: 'Optimisation did not finish',
            description: message.payload.reason,
            variant: 'destructive',
          });
          break;

        case 'connectivity.warning':
          state.degradeSystem(
            message.payload.system,
            message.payload.name,
            message.payload.message,
          );
          break;

        case 'connectivity.restored':
          state.restoreSystem(message.payload.system);
          state.pushToast({
            title: `${message.payload.name ?? message.payload.system} reconnected`,
            variant: 'success',
          });
          break;

        case 'orders.pending':
          state.setPendingOrders(message.payload.pending_count);
          void queryClient.invalidateQueries({ queryKey: ['summary'] });
          break;

        case 'reoptimisation.suggested':
          state.setReoptimiseHint(message.payload.message);
          void queryClient.invalidateQueries({ queryKey: ['routes'] });
          break;

        case 'fms.notification':
          state.pushToast({
            title: 'Fleet update needs attention',
            description: message.payload.message,
            variant: 'warning',
          });
          break;

        default:
          break;
      }
    });

    return () => {
      unsubscribe();
      roeSocket.disconnect();
    };
  }, [token, queryClient]);
}
