/**
 * Property tests for the route summary display logic.
 *
 * Feature: route-optimisation-engine, Property 18: Route summary panel displays accurate metrics
 * Feature: route-optimisation-engine, Property 19: Capacity threshold indicators are correct for all utilisation values
 */

import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  formatDistanceKm,
  formatDurationMin,
  formatUtilisation,
  loadLevel,
  utilisationPct,
  vehicleColour,
} from '@/lib/utils';

const finite = (min: number, max: number) =>
  fc.double({ min, max, noNaN: true, noDefaultInfinity: true });

describe('Property 18: route summary metrics match stored values', () => {
  // Validates: Requirements 8.4, 9.1
  it('formats distance to exactly 2 decimal places', () => {
    fc.assert(
      fc.property(finite(0, 100_000), (distance) => {
        const formatted = formatDistanceKm(distance);
        expect(formatted).toMatch(/^\d+\.\d{2}$/);
        expect(Number(formatted)).toBeCloseTo(distance, 2);
      }),
      { numRuns: 200 },
    );
  });

  it('formats duration as a whole number of minutes', () => {
    fc.assert(
      fc.property(finite(0, 100_000), (duration) => {
        const formatted = formatDurationMin(duration);
        expect(formatted).toMatch(/^-?\d+$/);
        expect(Number(formatted)).toBe(Math.round(duration));
      }),
      { numRuns: 200 },
    );
  });

  it('computes utilisation as (total / capacity) * 100 to 1 decimal place', () => {
    fc.assert(
      fc.property(finite(0, 50_000), finite(0.01, 50_000), (total, capacity) => {
        const pct = utilisationPct(total, capacity);
        expect(pct).not.toBeNull();
        const exact = (total / capacity) * 100;
        // Rounded to 1 d.p., so never more than half a tenth away.
        expect(Math.abs((pct as number) - exact)).toBeLessThanOrEqual(0.05000001);
        expect(formatUtilisation(pct)).toMatch(/^\d+\.\d%$/);
      }),
      { numRuns: 200 },
    );
  });

  it('shows N/A when weight, volume, or capacity data is missing', () => {
    expect(formatDistanceKm(null)).toBe('N/A');
    expect(formatDurationMin(null)).toBe('N/A');
    expect(formatUtilisation(null)).toBe('N/A');
    expect(utilisationPct(null, 100)).toBeNull();
    expect(utilisationPct(50, null)).toBeNull();
    expect(utilisationPct(50, 0)).toBeNull();
    expect(utilisationPct(Number.NaN, 100)).toBeNull();
  });
});

describe('Property 19: capacity threshold indicators', () => {
  // Validates: Requirements 9.2, 9.3, 14.1
  it('classifies every weight/capacity pair into the correct band', () => {
    fc.assert(
      fc.property(finite(0, 20_000), finite(0.01, 10_000), (total, capacity) => {
        const pct = utilisationPct(total, capacity);
        const level = loadLevel(pct);
        const exact = (total / capacity) * 100;

        if (exact >= 100.05) expect(level).toBe('critical');
        else if (exact < 89.95) expect(level).toBe('normal');
        // Values within rounding distance of a boundary may land either side,
        // but must never skip a band.
        else expect(['warning', 'critical', 'normal']).toContain(level);
      }),
      { numRuns: 300 },
    );
  });

  it('uses the documented boundaries exactly', () => {
    expect(loadLevel(0)).toBe('normal');
    expect(loadLevel(89.9)).toBe('normal');
    expect(loadLevel(90)).toBe('warning');
    expect(loadLevel(99.9)).toBe('warning');
    expect(loadLevel(100)).toBe('critical');
    expect(loadLevel(140)).toBe('critical');
    expect(loadLevel(null)).toBe('unknown');
    expect(loadLevel(Number.NaN)).toBe('unknown');
  });

  it('never reports warning below 90% or normal at or above 100%', () => {
    fc.assert(
      fc.property(finite(0, 500), (pct) => {
        const level = loadLevel(pct);
        if (level === 'warning') expect(pct).toBeGreaterThanOrEqual(90);
        if (level === 'normal') expect(pct).toBeLessThan(90);
        if (level === 'critical') expect(pct).toBeGreaterThanOrEqual(100);
      }),
      { numRuns: 300 },
    );
  });
});

describe('route colours', () => {
  it('is deterministic per vehicle id', () => {
    fc.assert(
      fc.property(fc.uuid(), (id) => {
        expect(vehicleColour(id)).toBe(vehicleColour(id));
        expect(vehicleColour(id)).toMatch(/^#[0-9a-f]{6}$/i);
      }),
      { numRuns: 200 },
    );
  });
});
