/**
 * Drop targets for reassignment.
 * Validates: Requirements 10.1, 10.3, 10.6
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ReassignTargets } from '@/components/panels/ReassignTargets';
import { DRAG_MIME } from '@/components/panels/RouteDetailPanel';
import { makeRoute } from '@/test/factories';

function dropOn(element: HTMLElement, payload: unknown) {
  const event = new Event('drop', { bubbles: true }) as Event & { dataTransfer: unknown };
  event.dataTransfer = {
    types: [DRAG_MIME],
    getData: (type: string) => (type === DRAG_MIME ? JSON.stringify(payload) : ''),
    dropEffect: 'none',
  };
  element.dispatchEvent(event);
}

describe('ReassignTargets', () => {
  const payload = {
    orderId: 'order-1',
    sourceRouteId: 'route-current',
    address: '30 Raffles Place',
    weightKg: 25,
  };

  it('lists every route except the one being viewed', () => {
    const current = makeRoute({ route_id: 'route-current' });
    const other = makeRoute({ route_id: 'route-other', vehicle_registration: 'SG0002B' });
    render(
      <ReassignTargets
        routes={[current, other]}
        currentRouteId="route-current"
        onDropOrder={() => {}}
      />,
    );
    expect(screen.getByText('SG0002B')).toBeInTheDocument();
    expect(screen.queryByTestId('reassign-target-route-current')).not.toBeInTheDocument();
  });

  it('renders nothing when there is nowhere to move work', () => {
    const { container } = render(
      <ReassignTargets
        routes={[makeRoute({ route_id: 'only' })]}
        currentRouteId="only"
        onDropOrder={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the remaining headroom on each target', () => {
    render(
      <ReassignTargets
        routes={[
          makeRoute({ route_id: 'a' }),
          makeRoute({
            route_id: 'b',
            vehicle_capacity_weight_kg: 1000,
            total_weight_kg: 400,
          }),
        ]}
        currentRouteId="a"
        onDropOrder={() => {}}
      />,
    );
    expect(screen.getByText('600.0 kg free')).toBeInTheDocument();
  });

  it('marks locked and dispatched routes instead of offering them', () => {
    render(
      <ReassignTargets
        routes={[
          makeRoute({ route_id: 'a' }),
          makeRoute({ route_id: 'locked', locked: true, vehicle_registration: 'SGLOCK' }),
          makeRoute({
            route_id: 'gone',
            status: 'dispatched',
            locked: true,
            vehicle_registration: 'SGGONE',
          }),
        ]}
        currentRouteId="a"
        onDropOrder={() => {}}
      />,
    );
    expect(screen.getByText('Locked')).toBeInTheDocument();
    expect(screen.getByText('dispatched')).toBeInTheDocument();
  });

  it('orders targets by available headroom, blocked ones last', () => {
    render(
      <ReassignTargets
        routes={[
          makeRoute({ route_id: 'a' }),
          makeRoute({
            route_id: 'tight',
            vehicle_registration: 'SGTIGHT',
            vehicle_capacity_weight_kg: 500,
            total_weight_kg: 480,
          }),
          makeRoute({ route_id: 'blocked', vehicle_registration: 'SGBLOCK', locked: true }),
          makeRoute({
            route_id: 'roomy',
            vehicle_registration: 'SGROOMY',
            vehicle_capacity_weight_kg: 2000,
            total_weight_kg: 100,
          }),
        ]}
        currentRouteId="a"
        onDropOrder={() => {}}
      />,
    );
    const order = screen.getAllByText(/^SG(TIGHT|BLOCK|ROOMY)$/).map((n) => n.textContent);
    expect(order).toEqual(['SGROOMY', 'SGTIGHT', 'SGBLOCK']);
  });

  it('reports the drop to the caller with the destination route', () => {
    const onDropOrder = vi.fn();
    render(
      <ReassignTargets
        routes={[makeRoute({ route_id: 'a' }), makeRoute({ route_id: 'dest' })]}
        currentRouteId="a"
        onDropOrder={onDropOrder}
      />,
    );
    dropOn(screen.getByTestId('reassign-target-dest'), payload);
    expect(onDropOrder).toHaveBeenCalledWith(payload, 'dest');
  });

  it('ignores a drop carrying no payload', () => {
    const onDropOrder = vi.fn();
    render(
      <ReassignTargets
        routes={[makeRoute({ route_id: 'a' }), makeRoute({ route_id: 'dest' })]}
        currentRouteId="a"
        onDropOrder={onDropOrder}
      />,
    );
    const target = screen.getByTestId('reassign-target-dest');
    const event = new Event('drop', { bubbles: true }) as Event & { dataTransfer: unknown };
    event.dataTransfer = { types: [], getData: () => '', dropEffect: 'none' };
    target.dispatchEvent(event);
    expect(onDropOrder).not.toHaveBeenCalled();
  });
});
