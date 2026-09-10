/**
 * Alert panel behaviour.
 * Validates: Requirements 14.4, 14.5
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { AlertPanel } from '@/components/panels/AlertPanel';
import { makeAlert } from '@/test/factories';

describe('AlertPanel', () => {
  it('shows type, severity, entity reference, and timestamp for each alert', () => {
    const alert = makeAlert({
      alert_type: 'late_delivery',
      severity: 'warning',
      entity_type: 'order',
      entity_id: 'abcdef01-2345-6789-abcd-ef0123456789',
      message: 'Stop 3 arrives after its window closes',
    });
    render(<AlertPanel alerts={[alert]} isLoading={false} onAcknowledge={() => {}} />);

    expect(screen.getByText('Late delivery')).toBeInTheDocument();
    expect(screen.getByText('warning')).toBeInTheDocument();
    expect(screen.getByText('Stop 3 arrives after its window closes')).toBeInTheDocument();
    expect(screen.getByText(/order: abcdef01/)).toBeInTheDocument();
  });

  it('acknowledges an alert on click', async () => {
    const onAcknowledge = vi.fn();
    const alert = makeAlert();
    render(<AlertPanel alerts={[alert]} isLoading={false} onAcknowledge={onAcknowledge} />);

    await userEvent.click(screen.getByRole('button', { name: /acknowledge/i }));
    expect(onAcknowledge).toHaveBeenCalledWith(alert.alert_id);
  });

  it('renders an empty state when there is nothing to action', () => {
    render(<AlertPanel alerts={[]} isLoading={false} onAcknowledge={() => {}} />);
    expect(screen.getByText('No open alerts')).toBeInTheDocument();
  });

  it('distinguishes each alert type by label', () => {
    render(
      <AlertPanel
        alerts={[
          makeAlert({ alert_type: 'overload' }),
          makeAlert({ alert_type: 'impossible_order' }),
          makeAlert({ alert_type: 'export_failure' }),
        ]}
        isLoading={false}
        onAcknowledge={() => {}}
      />,
    );
    expect(screen.getByText('Vehicle overloaded')).toBeInTheDocument();
    expect(screen.getByText('Order cannot be planned')).toBeInTheDocument();
    expect(screen.getByText('Export failed')).toBeInTheDocument();
  });
});
