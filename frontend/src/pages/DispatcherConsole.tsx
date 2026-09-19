/**
 * The dispatcher workspace: map, route summary, detail panels, alerts, and the
 * controls for optimisation, manual entry, and upload.
 */

import {
  Bell,
  FileSpreadsheet,
  ListChecks,
  Package,
  PlusCircle,
  Route as RouteIcon,
  ScrollText,
  Truck,
} from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import { AlertPanel } from '@/components/panels/AlertPanel';
import { AuditLogModal } from '@/components/panels/AuditLogModal';
import { OptimiseControls } from '@/components/panels/OptimiseControls';
import {
  RouteDetailPanel,
  StopDetailPanel,
  type DragPayload,
} from '@/components/panels/RouteDetailPanel';
import { ReassignTargets } from '@/components/panels/ReassignTargets';
import { RouteSummaryPanel } from '@/components/panels/RouteSummaryPanel';
import { ConnectivityBanner } from '@/components/layout/ConnectivityBanner';
import { MapView } from '@/components/map/MapView';
import { OrderForm } from '@/components/forms/OrderForm';
import { UploadDrawer } from '@/components/forms/UploadDrawer';
import { VehicleForm } from '@/components/forms/VehicleForm';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { api, ApiError } from '@/lib/api';
import { cn, pluralise } from '@/lib/utils';
import {
  findRoute,
  useAlerts,
  useConnectivity,
  useRouteMutations,
  useRoutes,
  useSummary,
} from '@/hooks/useRoeData';
import { selectAlerts, unacknowledgedAlerts, useAppStore } from '@/store';

type SidePanel = 'routes' | 'alerts';

const SIDE_PANEL_ID = 'side-panel';

interface PendingReassign extends DragPayload {
  destRouteId: string;
  lateOrderIds: string[];
  message: string;
}

export function DispatcherConsole() {
  const user = useAppStore((s) => s.user);
  const selectedRouteId = useAppStore((s) => s.selectedRouteId);
  const selectedStopId = useAppStore((s) => s.selectedStopId);
  const selectRoute = useAppStore((s) => s.selectRoute);
  const selectStop = useAppStore((s) => s.selectStop);
  const setLastRun = useAppStore((s) => s.setLastRun);
  const setRunProgress = useAppStore((s) => s.setRunProgress);
  const setReoptimiseHint = useAppStore((s) => s.setReoptimiseHint);
  const pushToast = useAppStore((s) => s.pushToast);
  const alerts = useAppStore(selectAlerts);

  const [panel, setPanel] = useState<SidePanel>('routes');
  const [entryOpen, setEntryOpen] = useState(false);
  const [auditOpen, setAuditOpen] = useState(false);
  const [pendingReassign, setPendingReassign] = useState<PendingReassign | null>(null);
  const [running, setRunning] = useState(false);

  const authed = Boolean(user);
  const routesQuery = useRoutes(authed);
  const alertsQuery = useAlerts(authed);
  useSummary(authed);
  useConnectivity(authed);

  const { patchRoute, exportRoute, recalculate, reassign, acknowledgeAlert, optimise } =
    useRouteMutations();

  const routes = useMemo(() => routesQuery.data ?? [], [routesQuery.data]);
  const unacknowledged = useMemo(() => unacknowledgedAlerts(alerts), [alerts]);
  const selectedRoute = findRoute(routes, selectedRouteId);
  const selectedStop = selectedRoute?.stops.find((s) => s.stop_id === selectedStopId) ?? null;
  const isAdmin = user?.role === 'administrator';

  // ---- optimisation ------------------------------------------------------
  const handleOptimise = useCallback(
    async (reoptimise: boolean) => {
      setRunning(true);
      setRunProgress({ runId: 'pending', pct: 2, message: 'Starting optimisation…' });
      setReoptimiseHint(null);
      try {
        const run = await optimise.mutateAsync(reoptimise);
        setLastRun(run);
        pushToast({
          title: reoptimise ? 'Re-optimisation complete' : 'Optimisation complete',
          description: `${pluralise(run.routes_created ?? 0, 'route')} planned · ${run.orders_assigned ?? 0} orders assigned · ${run.orders_unassigned ?? 0} unassigned${run.locked_excluded_count ? ` · ${pluralise(run.locked_excluded_count, 'locked route')} preserved` : ''}`,
          variant: 'success',
        });
      } catch (error) {
        if (error instanceof ApiError) {
          pushToast({
            title:
              error.status === 409
                ? 'A run is already in progress'
                : error.status === 504
                  ? 'Optimisation timed out'
                  : 'Optimisation failed',
            description: error.message,
            variant: error.status === 409 ? 'warning' : 'destructive',
          });
        } else {
          pushToast({ title: 'Optimisation failed', variant: 'destructive' });
        }
      } finally {
        setRunning(false);
        setRunProgress(null);
      }
    },
    [optimise, pushToast, setLastRun, setReoptimiseHint, setRunProgress],
  );

  // ---- reassignment ------------------------------------------------------
  const submitReassign = useCallback(
    async (payload: DragPayload, destRouteId: string, confirmLate: boolean) => {
      try {
        const result = await reassign.mutateAsync({
          order_id: payload.orderId,
          source_route_id: payload.sourceRouteId,
          dest_route_id: destRouteId,
          confirm_late_delivery: confirmLate,
        });
        setPendingReassign(null);
        selectRoute(destRouteId);
        pushToast({
          title: 'Order reassigned',
          description: result.message,
          variant: result.late_stops.length ? 'warning' : 'success',
        });
      } catch (error) {
        if (error instanceof ApiError) {
          // Requirement 10.5 — a late-delivery consequence needs confirmation.
          if (error.body.error === 'confirmation_required') {
            setPendingReassign({
              ...payload,
              destRouteId,
              lateOrderIds: (error.body.details?.late_order_ids ?? []) as string[],
              message: error.message,
            });
            return;
          }
          setPendingReassign(null);
          pushToast({
            title:
              error.body.error === 'capacity_violation'
                ? 'Capacity exceeded'
                : error.body.error === 'route_locked'
                  ? 'Route is locked'
                  : 'Reassignment rejected',
            description: error.message,
            variant: 'destructive',
          });
        } else {
          setPendingReassign(null);
          pushToast({ title: 'Reassignment failed', variant: 'destructive' });
        }
      }
    },
    [reassign, pushToast, selectRoute],
  );

  const handleDropOrder = useCallback(
    (payload: DragPayload, destRouteId: string) => {
      void submitReassign(payload, destRouteId, false);
    },
    [submitReassign],
  );

  // ---- route actions -----------------------------------------------------
  const runRouteAction = useCallback(
    async (action: () => Promise<unknown>, success: string) => {
      try {
        await action();
        pushToast({ title: success, variant: 'success' });
      } catch (error) {
        pushToast({
          title: 'Action failed',
          description: error instanceof ApiError ? error.message : undefined,
          variant: 'destructive',
        });
      }
    },
    [pushToast],
  );

  const handleAcknowledge = useCallback(
    async (alertId: string) => {
      try {
        await acknowledgeAlert.mutateAsync(alertId);
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          pushToast({
            title: 'Already acknowledged',
            description: 'Another dispatcher acknowledged this alert first.',
            variant: 'warning',
          });
          void alertsQuery.refetch();
        } else {
          pushToast({ title: 'Could not acknowledge the alert', variant: 'destructive' });
        }
      }
    },
    [acknowledgeAlert, alertsQuery, pushToast],
  );

  return (
    <div className="flex h-full flex-col">
      <a href="#main-content" className="sr-only-focusable absolute left-2 top-2 z-50 rounded bg-primary px-3 py-1.5 text-sm text-primary-foreground">
        Skip to main content
      </a>

      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border bg-card px-4 py-2">
        <div className="flex items-center gap-2">
          <div className="rounded-lg bg-primary p-1.5 text-primary-foreground">
            <RouteIcon className="h-4 w-4" />
          </div>
          <div>
            <h1 className="text-sm font-semibold leading-tight">Route Optimisation Engine</h1>
            <p className="text-[11px] leading-tight text-muted-foreground">
              {routes.length ? `${pluralise(routes.length, 'route')} planned` : 'No routes planned'}
            </p>
          </div>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <Button variant="outline" size="sm" onClick={() => setEntryOpen(true)}>
            <PlusCircle className="h-3.5 w-3.5" />
            Add data
          </Button>
          {isAdmin && (
            <Button variant="outline" size="sm" onClick={() => setAuditOpen(true)}>
              <ScrollText className="h-3.5 w-3.5" />
              Audit log
            </Button>
          )}
          <div className="mx-1 hidden items-center gap-1.5 sm:flex">
            <Badge variant="outline" className="capitalize">
              {user?.role}
            </Badge>
            <span className="max-w-[160px] truncate text-xs text-muted-foreground" title={user?.email}>
              {user?.email}
            </span>
          </div>
        </div>
      </header>

      <ConnectivityBanner />

      <main id="main-content" className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <section
          className="order-2 flex w-full min-w-0 shrink-0 flex-col border-t border-border bg-card lg:order-1 lg:h-auto lg:w-[380px] lg:border-r lg:border-t-0"
          aria-label="Route planning"
        >
          <OptimiseControls
            onOptimise={(reopt) => void handleOptimise(reopt)}
            running={running}
            hasRoutes={routes.length > 0}
          />

          <div
            className="flex shrink-0 border-b border-border"
            role="tablist"
            aria-label="Side panel"
          >
            <PanelTab
              id="routes"
              active={panel === 'routes'}
              onClick={() => setPanel('routes')}
              icon={<ListChecks className="h-3.5 w-3.5" />}
              label="Routes"
              count={routes.length}
            />
            <PanelTab
              id="alerts"
              active={panel === 'alerts'}
              onClick={() => setPanel('alerts')}
              icon={<Bell className="h-3.5 w-3.5" />}
              label="Alerts"
              count={unacknowledged.length}
              emphasise={unacknowledged.some((a) => a.severity === 'critical')}
            />
          </div>

          {/* One panel whose content swaps, so every tab points at the same
              id and the panel names itself after whichever tab is selected. */}
          <div
            className="min-h-0 flex-1 overflow-hidden"
            role="tabpanel"
            id={SIDE_PANEL_ID}
            aria-labelledby={`tab-${panel}`}
            tabIndex={-1}
          >
            {panel === 'alerts' ? (
              <div className="h-full overflow-y-auto">
                <AlertPanel
                  alerts={unacknowledged}
                  isLoading={alertsQuery.isLoading}
                  onAcknowledge={(id) => void handleAcknowledge(id)}
                  acknowledgingId={acknowledgeAlert.isPending ? acknowledgeAlert.variables : null}
                  onSelectEntity={(alert) => {
                    const routeId = alert.context?.route_id as string | undefined;
                    if (routeId) {
                      selectRoute(routeId);
                      setPanel('routes');
                    }
                  }}
                />
              </div>
            ) : selectedRoute ? (
              <div className="flex h-full flex-col">
                <div className="min-h-0 flex-1 overflow-y-auto">
                  <RouteDetailPanel
                    route={selectedRoute}
                    onBack={() => selectRoute(null)}
                    busy={patchRoute.isPending || exportRoute.isPending || recalculate.isPending}
                    onLockToggle={(locked) =>
                      void runRouteAction(
                        () => patchRoute.mutateAsync({ routeId: selectedRoute.route_id, patch: { locked } }),
                        locked ? 'Route locked' : 'Route unlocked',
                      )
                    }
                    onApprove={() =>
                      void runRouteAction(
                        () =>
                          patchRoute.mutateAsync({
                            routeId: selectedRoute.route_id,
                            patch: { status: 'approved' },
                          }),
                        'Route approved and sent to the delivery platform',
                      )
                    }
                    onExport={() =>
                      void runRouteAction(
                        () => exportRoute.mutateAsync(selectedRoute.route_id),
                        'Export re-triggered',
                      )
                    }
                    onRecalculate={() =>
                      void runRouteAction(
                        () => recalculate.mutateAsync(selectedRoute.route_id),
                        'ETAs recalculated',
                      )
                    }
                    onDropOrder={handleDropOrder}
                  />
                </div>
                <ReassignTargets
                  routes={routes}
                  currentRouteId={selectedRoute.route_id}
                  onDropOrder={handleDropOrder}
                />
                {selectedStop && (
                  <StopDetailPanel
                    stop={selectedStop}
                    route={selectedRoute}
                    onClose={() => selectStop(null)}
                  />
                )}
              </div>
            ) : (
              <RouteSummaryPanel
                routes={routes}
                isLoading={routesQuery.isLoading}
                isError={routesQuery.isError}
                onRetry={() => void routesQuery.refetch()}
              />
            )}
          </div>
        </section>

        <section className="order-1 min-h-[320px] flex-1 lg:order-2" aria-label="Route map">
          <MapView
            routes={routes}
            isLoading={routesQuery.isLoading}
            isError={routesQuery.isError}
            onRetry={() => void routesQuery.refetch()}
          />
        </section>
      </main>

      <DataEntryDialog
        open={entryOpen}
        onOpenChange={setEntryOpen}
        onChanged={() => {
          void routesQuery.refetch();
          void alertsQuery.refetch();
        }}
      />

      <AuditLogModal open={auditOpen} onOpenChange={setAuditOpen} />

      <Dialog
        open={pendingReassign !== null}
        onOpenChange={(open) => !open && setPendingReassign(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>This move causes a late delivery</DialogTitle>
            <DialogDescription>{pendingReassign?.message}</DialogDescription>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">
            {pendingReassign
              ? `${pluralise(pendingReassign.lateOrderIds.length, 'order')} on the destination route would arrive after its delivery window closes. Nothing has been changed yet.`
              : ''}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingReassign(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() =>
                pendingReassign &&
                void submitReassign(pendingReassign, pendingReassign.destRouteId, true)
              }
            >
              Reassign anyway
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function PanelTab({
  id,
  active,
  onClick,
  icon,
  label,
  count,
  emphasise,
}: {
  id: string;
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  count: number;
  emphasise?: boolean;
}) {
  return (
    <button
      type="button"
      role="tab"
      id={`tab-${id}`}
      aria-controls={SIDE_PANEL_ID}
      aria-selected={active}
      // Only the active tab is in the tab order; arrow keys move between tabs,
      // which is the expected keyboard model for a tablist.
      tabIndex={active ? 0 : -1}
      onKeyDown={(event) => {
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault();
          const tabs = Array.from(
            event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
              '[role="tab"]',
            ) ?? [],
          );
          const index = tabs.indexOf(event.currentTarget);
          const next = tabs[(index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
          next?.focus();
          next?.click();
        }
      }}
      onClick={onClick}
      className={cn(
        'flex flex-1 items-center justify-center gap-1.5 border-b-2 px-3 py-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
        active
          ? 'border-primary text-foreground'
          : 'border-transparent text-muted-foreground hover:text-foreground',
      )}
    >
      {icon}
      {label}
      {count > 0 && (
        <Badge
          variant={emphasise ? 'destructive' : 'secondary'}
          className="px-1.5 py-0 text-[10px] tabular-nums"
        >
          {count}
        </Badge>
      )}
    </button>
  );
}

function DataEntryDialog({
  open,
  onOpenChange,
  onChanged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged: () => void;
}) {
  const pushToast = useAppStore((s) => s.pushToast);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add orders and vehicles</DialogTitle>
          <DialogDescription>
            Enter records by hand or bulk-import a spreadsheet when an integration is unavailable.
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="order" className="min-h-0 flex-1 overflow-y-auto">
          <TabsList className="mb-3">
            <TabsTrigger value="order">
              <Package className="h-3.5 w-3.5" />
              Order
            </TabsTrigger>
            <TabsTrigger value="vehicle">
              <Truck className="h-3.5 w-3.5" />
              Vehicle
            </TabsTrigger>
            <TabsTrigger value="upload-orders">
              <FileSpreadsheet className="h-3.5 w-3.5" />
              Upload orders
            </TabsTrigger>
            <TabsTrigger value="upload-vehicles">
              <FileSpreadsheet className="h-3.5 w-3.5" />
              Upload vehicles
            </TabsTrigger>
          </TabsList>

          <TabsContent value="order">
            <OrderForm
              onSubmit={(payload) => api.createOrder(payload)}
              onCreated={(order) => {
                pushToast({
                  title: 'Order created',
                  description: `Reference ${order.order_id}`,
                  variant: 'success',
                });
                onChanged();
              }}
            />
          </TabsContent>

          <TabsContent value="vehicle">
            <VehicleForm
              onSubmit={(payload) => api.createVehicle(payload)}
              onCreated={(vehicle) => {
                pushToast({
                  title: 'Vehicle created',
                  description: `${vehicle.registration} · ${vehicle.vehicle_id}`,
                  variant: 'success',
                });
                onChanged();
              }}
            />
          </TabsContent>

          <TabsContent value="upload-orders">
            <UploadDrawer
              kind="orders"
              onImported={(result) => {
                pushToast({
                  title: `Imported ${pluralise(result.imported, 'order')}`,
                  description: result.skipped ? `${result.skipped} row(s) skipped` : undefined,
                  variant: result.skipped ? 'warning' : 'success',
                });
                onChanged();
              }}
            />
          </TabsContent>

          <TabsContent value="upload-vehicles">
            <UploadDrawer
              kind="vehicles"
              onImported={(result) => {
                pushToast({
                  title: `Imported ${pluralise(result.imported, 'vehicle')}`,
                  description: result.skipped ? `${result.skipped} row(s) skipped` : undefined,
                  variant: result.skipped ? 'warning' : 'success',
                });
                onChanged();
              }}
            />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
