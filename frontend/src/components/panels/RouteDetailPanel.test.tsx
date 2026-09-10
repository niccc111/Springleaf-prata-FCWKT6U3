/**
 * Route detail panel: metrics, lifecycle controls, and drag-and-drop reassignment.
 * Validates: Requirements 8.4, 8.5, 10.1, 10.6, 11.1, 11.5, 13.1
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DRAG_MIME, RouteDetailPanel, StopDetailPanel } from '@/components/panels/RouteDetailPanel';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useAppStore } from '@/store';
import { makeOrderSummary, makeRoute, makeStop } from '@/test/factories';

const noop = () => {};

function renderDetail(route: ReturnType<typeof makeRoute>, overrides = {}) {
  const props = {
    route,
    onBack: noop,
    onLockToggle: noop,
    onApprove: noop,
    onExport: noop,
    onRecalculate: noop,
    onDropOrder: noop,
    ...overrides,
  };
  return render(
    <TooltipProvider>
      <RouteDetailPanel {...props} />
    </TooltipProvider>,
  );
}

describe('RouteDetailPanel', () => {
  beforeEach(() => {
    useAppStore.setState({ selectedStopId: null, selectedRouteId: null });
  });

  it('shows the route summary metrics for the selected route', () => {
    renderDetail(
      makeRoute({
        vehicle_registration: 'SG4321B',
        total_distance_km: 31.2,
        total_duration_min: 95,
        total_weight_kg: 620,
      }),
    );
    expect(screen.getByText('SG4321B')).toBeInTheDocument();
    expect(screen.getByTestId('detail-distance')).toHaveTextContent('31.20 km');
    expect(screen.getByTestId('detail-duration')).toHaveTextContent('95 min');
    expect(screen.getByTestId('detail-weight')).toHaveTextContent('620.0 kg');
  });

  it('offers approve on a draft route and re-send on an approved one', () => {
    const { unmount } = renderDetail(makeRoute({ status: 'draft' }));
    expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument();
    unmount();

    renderDetail(makeRoute({ status: 'approved' }));
    expect(screen.getByRole('button', { name: /re-send/i })).toBeInTheDocument();
  });

  it('disables the lock control once the route is dispatched', () => {
    renderDetail(makeRoute({ status: 'dispatched', locked: true }));
    expect(screen.getByRole('button', { name: /unlock/i })).toBeDisabled();
  });

  it('locks and unlocks through the callback', async () => {
    const onLockToggle = vi.fn();
    renderDetail(makeRoute({ locked: false }), { onLockToggle });
    await userEvent.click(screen.getByRole('button', { name: /^lock$/i }));
    expect(onLockToggle).toHaveBeenCalledWith(true);
  });

  it('accepts a dropped order from another route', async () => {
    const onDropOrder = vi.fn();
    const route = makeRoute({ route_id: 'dest-route' });
    renderDetail(route, { onDropOrder });

    const payload = {
      orderId: 'order-9',
      sourceRouteId: 'other-route',
      address: '9 Bishan Place',
      weightKg: 20,
    };
    const container = screen.getByText(/drag a stop onto another route/i).closest('div')
      ?.parentElement as HTMLElement;

    const dataTransfer = {
      types: [DRAG_MIME],
      getData: (type: string) => (type === DRAG_MIME ? JSON.stringify(payload) : ''),
      dropEffect: 'none',
    };
    const dropEvent = new Event('drop', { bubbles: true }) as Event & { dataTransfer: unknown };
    dropEvent.dataTransfer = dataTransfer;
    container.dispatchEvent(dropEvent);

    expect(onDropOrder).toHaveBeenCalledWith(payload, 'dest-route');
  });

  it('explains why a locked route rejects changes', () => {
    renderDetail(makeRoute({ locked: true }));
    expect(screen.getByText(/this route is locked/i)).toBeInTheDocument();
  });

  it('surfaces a data-quality warning on the route', () => {
    renderDetail(
      makeRoute({ data_quality_warning: true, data_quality_message: 'Historical averages in use' }),
    );
    expect(screen.getByText('Historical averages in use')).toBeInTheDocument();
  });

  it('marks late stops', () => {
    renderDetail(
      makeRoute({
        stops: [makeStop({ late: true, time_window_end: '2026-06-01T09:00:00Z' })],
      }),
    );
    expect(screen.getByText('Late')).toBeInTheDocument();
  });
});

describe('StopDetailPanel', () => {
  it('lists order ids, cargo weight, window, and ETA for the stop', () => {
    const orders = [
      makeOrderSummary({ order_id: 'o-1', external_ref: 'OMS-00042', cargo_weight_kg: 30 }),
      makeOrderSummary({ order_id: 'o-2', cargo_weight_kg: 20, priority: 'priority' }),
    ];
    const stop = makeStop({
      orders,
      total_weight_kg: 50,
      time_window_start: '2026-06-01T09:00:00Z',
      time_window_end: '2026-06-01T12:00:00Z',
      eta: '2026-06-01T09:30:00Z',
    });
    render(<StopDetailPanel stop={stop} route={makeRoute({ stops: [stop] })} onClose={() => {}} />);

    expect(screen.getByText('OMS-00042')).toBeInTheDocument();
    expect(screen.getByText('50.0 kg')).toBeInTheDocument();
    expect(screen.getByText('Priority')).toBeInTheDocument();
    expect(screen.getByText('Orders at this stop')).toBeInTheDocument();
  });
});
