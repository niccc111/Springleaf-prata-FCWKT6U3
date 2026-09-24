/** Builds the GeoJSON sources that drive the map layers. */

import type { Feature, FeatureCollection, LineString, Point } from 'geojson';
import type { Route } from '@/types';
import { vehicleColour } from '@/lib/utils';

export interface RouteLineProps {
  route_id: string;
  vehicle_id: string;
  colour: string;
  registration: string;
  locked: boolean;
  status: string;
}

export interface StopPointProps {
  stop_id: string;
  route_id: string;
  vehicle_id: string;
  colour: string;
  sequence: number;
  address: string;
  eta: string;
  late: boolean;
  priority: boolean;
  order_count: number;
  weight: number;
}

export interface DepotPointProps {
  vehicle_id: string;
  route_id: string;
  colour: string;
  registration: string;
}

const empty = <T>(): FeatureCollection<never, T> =>
  ({ type: 'FeatureCollection', features: [] }) as unknown as FeatureCollection<never, T>;

/**
 * Stable numeric feature id derived from a UUID. MapLibre's `setFeatureState`
 * needs an `id` on the feature itself, and it must survive re-renders so
 * selection highlighting does not flicker.
 */
export function featureId(id: string): number {
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return hash;
}

export function routeLines(routes: Route[]): FeatureCollection<LineString, RouteLineProps> {
  const features: Feature<LineString, RouteLineProps>[] = [];
  for (const route of routes) {
    if (route.stops.length === 0) continue;
    const colour = vehicleColour(route.vehicle_id);
    const ordered = [...route.stops].sort((a, b) => a.sequence_number - b.sequence_number);

    // Prefer the road-following geometry from the routing engine (a dense list
    // of [lon, lat] points along the streets). Fall back to straight segments
    // between depot and stops when no road geometry is available.
    let coordinates: [number, number][] = [];
    const roadCoords = route.geometry?.coordinates;
    if (Array.isArray(roadCoords) && roadCoords.length >= 2) {
      coordinates = roadCoords as [number, number][];
    } else {
      if (route.depot_location) {
        coordinates.push([route.depot_location.longitude, route.depot_location.latitude]);
      }
      for (const stop of ordered) {
        coordinates.push([stop.location.longitude, stop.location.latitude]);
      }
      if (route.depot_location) {
        coordinates.push([route.depot_location.longitude, route.depot_location.latitude]);
      }
    }
    if (coordinates.length < 2) continue;
    features.push({
      type: 'Feature',
      id: featureId(route.route_id),
      geometry: { type: 'LineString', coordinates },
      properties: {
        route_id: route.route_id,
        vehicle_id: route.vehicle_id,
        colour,
        registration: route.vehicle_registration ?? 'Unknown vehicle',
        locked: route.locked,
        status: route.status,
      },
    });
  }
  return features.length
    ? { type: 'FeatureCollection', features }
    : empty<RouteLineProps>();
}

export function stopPoints(routes: Route[]): FeatureCollection<Point, StopPointProps> {
  const features: Feature<Point, StopPointProps>[] = [];
  for (const route of routes) {
    const colour = vehicleColour(route.vehicle_id);
    for (const stop of route.stops) {
      features.push({
        type: 'Feature',
        id: featureId(stop.stop_id),
        geometry: { type: 'Point', coordinates: [stop.location.longitude, stop.location.latitude] },
        properties: {
          stop_id: stop.stop_id,
          route_id: route.route_id,
          vehicle_id: route.vehicle_id,
          colour,
          sequence: stop.sequence_number,
          address: stop.address,
          eta: stop.eta,
          late: stop.late,
          priority: stop.has_priority_order,
          order_count: stop.order_ids.length,
          weight: stop.total_weight_kg,
        },
      });
    }
  }
  return features.length ? { type: 'FeatureCollection', features } : empty<StopPointProps>();
}

export function depotPoints(routes: Route[]): FeatureCollection<Point, DepotPointProps> {
  const seen = new Set<string>();
  const features: Feature<Point, DepotPointProps>[] = [];
  for (const route of routes) {
    if (!route.depot_location) continue;
    const key = `${route.depot_location.latitude},${route.depot_location.longitude}`;
    if (seen.has(key)) continue;
    seen.add(key);
    features.push({
      type: 'Feature',
      id: featureId(key),
      geometry: {
        type: 'Point',
        coordinates: [route.depot_location.longitude, route.depot_location.latitude],
      },
      properties: {
        vehicle_id: route.vehicle_id,
        route_id: route.route_id,
        colour: vehicleColour(route.vehicle_id),
        registration: route.vehicle_registration ?? 'Depot',
      },
    });
  }
  return features.length ? { type: 'FeatureCollection', features } : empty<DepotPointProps>();
}

/** Bounding box [west, south, east, north] over every rendered feature. */
export function boundsOf(routes: Route[]): [number, number, number, number] | null {
  let west = 180;
  let south = 90;
  let east = -180;
  let north = -90;
  let seen = false;

  const visit = (lon: number, lat: number) => {
    seen = true;
    west = Math.min(west, lon);
    east = Math.max(east, lon);
    south = Math.min(south, lat);
    north = Math.max(north, lat);
  };

  for (const route of routes) {
    if (route.depot_location) visit(route.depot_location.longitude, route.depot_location.latitude);
    for (const stop of route.stops) visit(stop.location.longitude, stop.location.latitude);
  }
  return seen ? [west, south, east, north] : null;
}
