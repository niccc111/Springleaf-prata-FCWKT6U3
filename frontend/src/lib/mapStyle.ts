/**
 * MapLibre style resolution.
 *
 * The tile source is configurable so a deployment can point at its own vector
 * or raster tiles. With no configuration the app falls back to a self-contained
 * offline style (no network, no credentials) so routes, stops, and depots are
 * always visible — a blank canvas beats a broken map for a dispatcher.
 */

import type { StyleSpecification } from 'maplibre-gl';

const RASTER_TILE_URL = import.meta.env.VITE_MAP_TILE_URL as string | undefined;
const RASTER_ATTRIBUTION =
  (import.meta.env.VITE_MAP_ATTRIBUTION as string | undefined) ??
  '© OpenStreetMap contributors';
export const STYLE_URL = import.meta.env.VITE_MAP_STYLE_URL as string | undefined;

/** Neutral grid background used when no tile source is configured. */
export const OFFLINE_STYLE: StyleSpecification = {
  version: 8,
  name: 'ROE offline',
  sources: {},
  layers: [
    {
      id: 'background',
      type: 'background',
      paint: { 'background-color': '#eef2f7' },
    },
  ],
};

export function rasterStyle(): StyleSpecification | null {
  if (!RASTER_TILE_URL) return null;
  return {
    version: 8,
    name: 'ROE raster',
    sources: {
      basemap: {
        type: 'raster',
        tiles: [RASTER_TILE_URL],
        tileSize: 256,
        attribution: RASTER_ATTRIBUTION,
        maxzoom: 19,
      },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': '#eef2f7' } },
      { id: 'basemap', type: 'raster', source: 'basemap', paint: { 'raster-opacity': 0.9 } },
    ],
  };
}

export function resolveStyle(): string | StyleSpecification {
  if (STYLE_URL) return STYLE_URL;
  return rasterStyle() ?? OFFLINE_STYLE;
}

export const hasBasemap = Boolean(STYLE_URL || RASTER_TILE_URL);

/** Default viewport: Singapore, the service area of the bundled demo data. */
export const DEFAULT_CENTER: [number, number] = [103.8198, 1.3521];
export const DEFAULT_ZOOM = 10.5;
