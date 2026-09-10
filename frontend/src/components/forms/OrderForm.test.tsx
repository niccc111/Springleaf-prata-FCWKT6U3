/**
 * Manual order form behaviour.
 *
 * Feature: route-optimisation-engine, Property 7: Manual form validation rejects invalid submissions atomically
 * Validates: Requirements 3.1, 3.3, 3.4, 3.5
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fc from 'fast-check';
import { describe, expect, it, vi } from 'vitest';
import { OrderForm } from '@/components/forms/OrderForm';
import { ApiError } from '@/lib/api';
import { EMPTY_ORDER, validateOrder } from '@/lib/validation';
import type { Order } from '@/types';

const okOrder = (): Order =>
  ({
    order_id: 'order-1',
    source: 'manual',
    delivery_address: '30 Raffles Place',
    cargo_weight_kg: 12,
  }) as unknown as Order;

describe('OrderForm', () => {
  it('blocks submission and names every invalid field', async () => {
    const onSubmit = vi.fn();
    render(<OrderForm onSubmit={onSubmit} onCreated={() => {}} />);

    await userEvent.click(screen.getByRole('button', { name: /create order/i }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(await screen.findByText('Delivery address is required')).toBeInTheDocument();
    expect(screen.getByText('Cargo weight is required')).toBeInTheDocument();
  });

  it('rejects an out-of-range weight without calling the API', async () => {
    const onSubmit = vi.fn();
    render(<OrderForm onSubmit={onSubmit} onCreated={() => {}} />);

    await userEvent.type(screen.getByLabelText(/delivery address/i), '30 Raffles Place');
    await userEvent.type(screen.getByLabelText(/cargo weight/i), '999999');
    await userEvent.click(screen.getByRole('button', { name: /create order/i }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(await screen.findByText(/between 0.01 and 99999.99/)).toBeInTheDocument();
  });

  it('preserves entered values and shows field errors when the server rejects', async () => {
    const onSubmit = vi.fn().mockRejectedValue(
      new ApiError(422, {
        error: 'validation_failed',
        message: 'One or more fields are invalid.',
        fields: [{ field: 'cargo_weight_kg', message: 'Must be between 0.01 and 99999.99' }],
      }),
    );
    render(<OrderForm onSubmit={onSubmit} onCreated={() => {}} />);

    const address = screen.getByLabelText(/delivery address/i);
    await userEvent.type(address, '9 Bishan Place');
    await userEvent.type(screen.getByLabelText(/cargo weight/i), '25');
    await userEvent.click(screen.getByRole('button', { name: /create order/i }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    // Requirement 3.3: entered values are preserved for retry.
    expect(address).toHaveValue('9 Bishan Place');
    expect(screen.getByLabelText(/cargo weight/i)).toHaveValue(25);
    expect(await screen.findByText('Must be between 0.01 and 99999.99')).toBeInTheDocument();
  });

  it('preserves entered values when persistence fails (Requirement 3.5)', async () => {
    const onSubmit = vi
      .fn()
      .mockRejectedValue(new ApiError(500, { error: 'persistence_failed', message: 'boom' }));
    render(<OrderForm onSubmit={onSubmit} onCreated={() => {}} />);

    await userEvent.type(screen.getByLabelText(/delivery address/i), '1 Depot Road');
    await userEvent.type(screen.getByLabelText(/cargo weight/i), '5');
    await userEvent.click(screen.getByRole('button', { name: /create order/i }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(screen.getByLabelText(/delivery address/i)).toHaveValue('1 Depot Road');
    expect(await screen.findByRole('alert')).toHaveTextContent('boom');
  });

  it('submits a valid order and reports the assigned identifier', async () => {
    const onSubmit = vi.fn().mockResolvedValue(okOrder());
    const onCreated = vi.fn();
    render(<OrderForm onSubmit={onSubmit} onCreated={onCreated} />);

    await userEvent.type(screen.getByLabelText(/delivery address/i), '30 Raffles Place');
    await userEvent.type(screen.getByLabelText(/cargo weight/i), '12');
    await userEvent.click(screen.getByRole('button', { name: /create order/i }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(okOrder()));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ delivery_address: '30 Raffles Place', cargo_weight_kg: 12 }),
    );
  });
});

describe('Property 7: client validation matches the server contract', () => {
  it('flags every invalid field and only invalid fields', () => {
    fc.assert(
      fc.property(
        fc.record({
          delivery_address: fc.oneof(fc.constant(''), fc.string({ minLength: 1, maxLength: 40 })),
          cargo_weight_kg: fc.oneof(
            fc.constant(''),
            fc.constant('0'),
            fc.constant('-3'),
            fc.constant('123456789'),
            fc.constant('12.5'),
          ),
          cargo_volume_m3: fc.oneof(fc.constant(''), fc.constant('-1'), fc.constant('0.4')),
        }),
        (values) => {
          const errors = validateOrder({ ...EMPTY_ORDER, ...values });

          const addressValid = values.delivery_address.trim().length > 0;
          expect('delivery_address' in errors).toBe(!addressValid);

          const weight = Number(values.cargo_weight_kg);
          const weightValid =
            values.cargo_weight_kg.trim().length > 0 &&
            Number.isFinite(weight) &&
            weight >= 0.01 &&
            weight <= 99999.99;
          expect('cargo_weight_kg' in errors).toBe(!weightValid);

          const volumeValid =
            values.cargo_volume_m3.trim().length === 0 || Number(values.cargo_volume_m3) >= 0;
          expect('cargo_volume_m3' in errors).toBe(!volumeValid);
        },
      ),
      { numRuns: 200 },
    );
  });
});
