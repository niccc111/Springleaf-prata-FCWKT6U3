/**
 * Orders side panel — a clear, always-visible list of every order with a
 * prominent delete button. Deleting is blocked by the server for orders on a
 * dispatched route (the API returns a validation error, surfaced as a toast).
 */

import { Loader2, PackageX, RefreshCw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { api, ApiError } from '@/lib/api';
import { useAppStore } from '@/store';
import type { Order } from '@/types';

export interface OrdersPanelProps {
  /** Bumped by the parent whenever orders may have changed, to trigger a refresh. */
  refreshKey?: number;
  /** Called after a successful delete so the parent can refresh routes/alerts. */
  onChanged?: () => void;
}

export function OrdersPanel({ refreshKey = 0, onChanged }: OrdersPanelProps) {
  const pushToast = useAppStore((s) => s.pushToast);
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Order | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const page = await api.listOrders({ limit: 500 });
      setOrders(page.items);
    } catch {
      setLoadError('Could not load orders. Try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshKey]);

  const confirmDelete = async (order: Order) => {
    setDeletingId(order.order_id);
    try {
      await api.deleteOrder(order.order_id);
      setOrders((prev) => prev.filter((o) => o.order_id !== order.order_id));
      pushToast({
        title: 'Order deleted',
        description: order.delivery_address,
        variant: 'success',
      });
      onChanged?.();
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : 'The order could not be deleted. Nothing was changed — try again.';
      pushToast({ title: 'Delete failed', description: message, variant: 'destructive' });
    } finally {
      setDeletingId(null);
      setPendingDelete(null);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <p className="text-xs text-muted-foreground">
          {orders.length} order{orders.length === 1 ? '' : 's'}
        </p>
        <Button variant="ghost" size="sm" onClick={() => void refresh()} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {loadError && (
          <p
            role="alert"
            className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs"
          >
            {loadError}
          </p>
        )}

        {!loadError && !loading && orders.length === 0 && (
          <div className="flex flex-col items-center gap-2 p-8 text-center text-muted-foreground">
            <PackageX className="h-6 w-6" />
            <p className="text-sm font-medium">No orders yet</p>
            <p className="text-xs">Add orders from the “Add” button, then optimise.</p>
          </div>
        )}

        <ul className="space-y-1.5">
          {orders.map((order) => (
            <li
              key={order.order_id}
              className="flex items-center justify-between gap-3 rounded-md border border-border bg-card p-2.5"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{order.delivery_address}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  <span className="capitalize">{order.priority}</span> ·{' '}
                  <span className="capitalize">{order.status}</span> · {order.cargo_weight_kg} kg
                </p>
              </div>
              <Button
                variant="destructive"
                size="sm"
                className="shrink-0"
                disabled={deletingId === order.order_id}
                onClick={() => setPendingDelete(order)}
                aria-label={`Delete order for ${order.delivery_address}`}
              >
                {deletingId === order.order_id ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" />
                )}
                Delete
              </Button>
            </li>
          ))}
        </ul>
      </div>

      <Dialog open={pendingDelete !== null} onOpenChange={(v) => !v && setPendingDelete(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this order?</DialogTitle>
            <DialogDescription>
              This permanently removes the order for{' '}
              <span className="font-medium">{pendingDelete?.delivery_address}</span>. This cannot be
              undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingDelete(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deletingId !== null}
              onClick={() => pendingDelete && void confirmDelete(pendingDelete)}
            >
              {deletingId ? 'Deleting…' : 'Delete order'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
