/**
 * Route summary panel rendering.
 *
 * Feature: route-optimisation-engine, Property 18: Route summary panel displays accurate metrics
 * Feature: route-optimisation-engine, Property 19: Capacity threshold indicators are correct for all utilisation values
 * Validates: Requirements 8.4, 9.1, 9.2, 9.3, 9.5
 */

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { RouteSummaryPanel } from '@/components/panels/RouteSummaryPanel';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useAppStore } from '@/store';
import { makeRoute, makeStop } from '@/test/factories';

function renderPanel(routes: ReturnType<typeof makeRoute>[]) {
  return render(
    <TooltipProvider>
      <RouteSummaryPanel routes={routes} isLoading={false} isError={false} onRetry={() => {}} />
    </TooltipProvider>,
  );
}

describe('RouteSummaryPanel', () => {
  beforeEach(() => {
    useAppStore.setState({ selectedRouteId: null, changedRouteIds: {} });
  });

  it('renders stored metrics at the documented precision', () => {
    const route = makeRoute({
      vehicle_registration: 'SG9999Z',
      total_distance_km: 24.567,
      total_duration_min: 78,
      total_weight_kg: 450,
      vehicle_capacity_weight_kg: 1000,
      weight_utilisation_pct: 45,
    });
    renderPanel([route]);

    expect(screen.getByText('SG9999Z')).toBeInTheDocument();
    // 2 d.p. distance, whole-number duration, 1 d.p. utilisation.
    expect(screen.getByText(/24\.57 km/)).toBeInTheDocument();
    expect(screen.getByText(/78 min/)).toBeInTheDocument();
    expect(screen.getByTestId('load-weight')).toHaveTextContent('45.0%');
  });

  it('shows no capacity indicator below 90%', () => {
    renderPanel([makeRoute({ weight_utilisation_pct: 62.5 })]);
    expect(screen.queryByTestId('indicator-warning')).not.toBeInTheDocument();
    expect(screen.queryByTestId('indicator-critical')).not.toBeInTheDocument();
  });

  it('shows a warning indicator between 90% and 100%', () => {
    renderPanel([makeRoute({ weight_utilisation_pct: 94.2 })]);
    expect(screen.getByTestId('indicator-warning')).toBeInTheDocument();
    expect(screen.queryByTestId('indicator-critical')).not.toBeInTheDocument();
  });

  it('replaces the warning with a critical indicator at or above 100%', () => {
    renderPanel([makeRoute({ weight_utilisation_pct: 104.8 })]);
    expect(screen.getByTestId('indicator-critical')).toBeInTheDocument();
    expect(screen.queryByTestId('indicator-warning')).not.toBeInTheDocument();
  });

  it('shows N/A and a data-quality marker when utilisation is unknown', () => {
    renderPanel([
      makeRoute({ weight_utilisation_pct: null, vehicle_capacity_weight_kg: null }),
    ]);
    expect(screen.getByTestId('load-weight')).toHaveTextContent('N/A');
    expect(screen.getByTestId('indicator-data-quality')).toBeInTheDocument();
  });

  it('flags routes whose travel times came from historical averages', () => {
    renderPanel([
      makeRoute({
        data_quality_warning: true,
        data_quality_message: 'Mapping Service unavailable',
      }),
    ]);
    expect(screen.getByTestId('indicator-data-quality-route')).toBeInTheDocument();
  });

  it('shows an empty state when no routes exist', () => {
    renderPanel([]);
    expect(screen.getByText('No routes planned')).toBeInTheDocument();
  });

  it('lists every stop across routes in the header count', () => {
    const routes = [
      makeRoute({ stops: [makeStop(), makeStop({ sequence_number: 2 })] }),
      makeRoute({ stops: [makeStop()] }),
    ];
    renderPanel(routes);
    expect(screen.getByText(/2 routes · 3 stops/)).toBeInTheDocument();
  });
});
