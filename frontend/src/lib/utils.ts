import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Deterministic route colour derived from the vehicle id (design § map). */
const ROUTE_PALETTE = [
  '#2563eb', '#dc2626', '#059669', '#d97706', '#7c3aed',
  '#0891b2', '#db2777', '#65a30d', '#ea580c', '#4f46e5',
  '#0d9488', '#c026d3', '#ca8a04', '#0284c7', '#e11d48',
  '#16a34a', '#9333ea', '#f59e0b', '#0369a1', '#be123c',
];

export function vehicleColour(vehicleId: string): string {
  let hash = 0;
  for (let i = 0; i < vehicleId.length; i += 1) {
    hash = (hash * 31 + vehicleId.charCodeAt(i)) >>> 0;
  }
  return ROUTE_PALETTE[hash % ROUTE_PALETTE.length];
}

/** Route summary formatting (Requirement 9.1 / Property 18). */
export function formatDistanceKm(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'N/A';
  return value.toFixed(2);
}

export function formatDurationMin(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'N/A';
  return String(Math.round(value));
}

export function formatUtilisation(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'N/A';
  return `${value.toFixed(1)}%`;
}

export function formatWeight(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'N/A';
  return `${value.toFixed(1)} kg`;
}

export function formatVolume(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'N/A';
  return `${value.toFixed(2)} m³`;
}

/** Utilisation percentage recomputed from stored values (Property 18). */
export function utilisationPct(total: number | null, capacity: number | null): number | null {
  if (total === null || capacity === null || !Number.isFinite(total) || !Number.isFinite(capacity)) {
    return null;
  }
  if (capacity <= 0) return null;
  return Math.round((total / capacity) * 1000) / 10;
}

export type LoadLevel = 'normal' | 'warning' | 'critical' | 'unknown';

/**
 * Capacity threshold indicator (Requirement 9.2/9.3, Property 19):
 * < 90% normal, 90%–<100% warning, >= 100% critical.
 */
export function loadLevel(utilisation: number | null | undefined): LoadLevel {
  if (utilisation === null || utilisation === undefined || !Number.isFinite(utilisation)) {
    return 'unknown';
  }
  if (utilisation >= 100) return 'critical';
  if (utilisation >= 90) return 'warning';
  return 'normal';
}

/**
 * Timezone used to display all times to the dispatcher. Times from the API are
 * absolute (UTC) instants; the operating region works in this zone. Configurable
 * via VITE_DISPLAY_TIMEZONE, defaulting to Singapore.
 */
export const DISPLAY_TIMEZONE =
  (import.meta.env.VITE_DISPLAY_TIMEZONE as string | undefined) ?? 'Asia/Singapore';

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: DISPLAY_TIMEZONE,
  });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString([], {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: DISPLAY_TIMEZONE,
  });
}

export function formatTimeWindow(start: string | null, end: string | null): string {
  if (!start && !end) return 'Any time';
  if (start && end) return `${formatTime(start)} – ${formatTime(end)}`;
  if (start) return `From ${formatTime(start)}`;
  return `By ${formatTime(end)}`;
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 45) return 'just now';
  if (seconds < 90) return '1 min ago';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

export function truncateId(id: string, length = 8): string {
  return id.length <= length ? id : `${id.slice(0, length)}…`;
}

export function pluralise(count: number, singular: string, plural?: string): string {
  return `${count} ${count === 1 ? singular : (plural ?? `${singular}s`)}`;
}
