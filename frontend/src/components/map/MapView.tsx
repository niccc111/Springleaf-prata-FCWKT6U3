/**
 * Interactive dispatcher map (Requirement 8).
 *
 * Renders one polyline per vehicle in a deterministic colour, a marker per stop
 * (clustered below zoom 12), and a distinct depot symbol per depot. Selection
 * is driven by `queryRenderedFeatures` on click; hover shows sequence number,
 * address, and ETA.
 */

import maplibregl, { type GeoJSONSource, type Map as MapLibreMap, type Popup } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { AlertTriangle, Crosshair, Layers, MapIcon, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { boundsOf, depotPoints, featureId, routeLines, stopPoints } from '@/lib/geojson';
import { DEFAULT_CENTER, DEFAULT_ZOOM, hasBasemap, resolveStyle } from '@/lib/mapStyle';
import { formatTime } from '@/lib/utils';
import { useAppStore } from '@/store';
import type { Route } from '@/types';

const SRC_ROUTES = 'roe-routes';
const SRC_STOPS = 'roe-stops';
const SRC_DEPOTS = 'roe-depots';

const LAYER_ROUTE_CASING = 'route-casing';
const LAYER_ROUTE_LINE = 'route-line';
const LAYER_STOP_CLUSTERS = 'stop-clusters';
const LAYER_STOP_CLUSTER_COUNT = 'stop-cluster-count';
const LAYER_STOP_POINTS = 'stop-points';
const LAYER_STOP_LABELS = 'stop-labels';
const LAYER_DEPOTS = 'depot-points';

export interface MapViewProps {
  routes: Route[];
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
}

export function MapView({ routes, isLoading, isError, onRetry }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const popupRef = useRef<Popup | null>(null);
  const [styleReady, setStyleReady] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);
  const [showLabels, setShowLabels] = useState(true);

  const selectedRouteId = useAppStore((s) => s.selectedRouteId);
  const selectedStopId = useAppStore((s) => s.selectedStopId);
  const selectRoute = useAppStore((s) => s.selectRoute);
  const selectStop = useAppStore((s) => s.selectStop);
  const setMapReady = useAppStore((s) => s.setMapReady);

  const data = useMemo(
    () => ({
      lines: routeLines(routes),
      stops: stopPoints(routes),
      depots: depotPoints(routes),
      bounds: boundsOf(routes),
    }),
    [routes],
  );

  // ---- map bootstrap -----------------------------------------------------
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let map: MapLibreMap;
    try {
      map = new maplibregl.Map({
        container: containerRef.current,
        style: resolveStyle(),
        center: DEFAULT_CENTER,
        zoom: DEFAULT_ZOOM,
        attributionControl: hasBasemap ? undefined : false,
      });
    } catch (error) {
      setMapError(error instanceof Error ? error.message : 'Map failed to initialise');
      return;
    }
    mapRef.current = map;

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-left');

    map.on('error', (event) => {
      // Tile and styling errors are recoverable — the route overlay still
      // renders over them, so they are logged rather than shown to the
      // dispatcher. Requirement 8.7's error state is driven by `isError`,
      // which reflects an actual failure to load the route data.
      const message = (event as unknown as { error?: Error }).error?.message ?? '';
      if (message) console.warn('[map]', message);
    });

    map.on('load', () => {
      map.addSource(SRC_ROUTES, { type: 'geojson', data: data.lines });
      map.addSource(SRC_STOPS, {
        type: 'geojson',
        data: data.stops,
        cluster: true,
        clusterRadius: 45,
        clusterMaxZoom: 11,
      });
      map.addSource(SRC_DEPOTS, { type: 'geojson', data: data.depots });

      map.addLayer({
        id: LAYER_ROUTE_CASING,
        type: 'line',
        source: SRC_ROUTES,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': '#ffffff',
          'line-width': ['case', ['boolean', ['feature-state', 'selected'], false], 8, 5],
          'line-opacity': 0.85,
        },
      });
      map.addLayer({
        id: LAYER_ROUTE_LINE,
        type: 'line',
        source: SRC_ROUTES,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': ['get', 'colour'],
          'line-width': ['case', ['boolean', ['feature-state', 'selected'], false], 5, 2.6],
          'line-opacity': [
            'case',
            ['boolean', ['feature-state', 'dimmed'], false],
            0.22,
            ['boolean', ['feature-state', 'selected'], false],
            1,
            0.8,
          ],
        },
      });

      map.addLayer({
        id: LAYER_STOP_CLUSTERS,
        type: 'circle',
        source: SRC_STOPS,
        filter: ['has', 'point_count'],
        paint: {
          'circle-color': '#1d4ed8',
          'circle-opacity': 0.85,
          'circle-radius': ['step', ['get', 'point_count'], 15, 10, 20, 30, 26],
          'circle-stroke-width': 2,
          'circle-stroke-color': '#ffffff',
        },
      });
      map.addLayer({
        id: LAYER_STOP_CLUSTER_COUNT,
        type: 'symbol',
        source: SRC_STOPS,
        filter: ['has', 'point_count'],
        layout: {
          'text-field': ['get', 'point_count_abbreviated'],
          'text-size': 12,
          'text-allow-overlap': true,
        },
        paint: { 'text-color': '#ffffff' },
      });
      map.addLayer({
        id: LAYER_STOP_POINTS,
        type: 'circle',
        source: SRC_STOPS,
        filter: ['!', ['has', 'point_count']],
        paint: {
          'circle-color': ['get', 'colour'],
          'circle-radius': ['case', ['boolean', ['feature-state', 'selected'], false], 11, 7],
          'circle-stroke-width': [
            'case',
            ['boolean', ['get', 'late'], false],
            3,
            ['boolean', ['get', 'priority'], false],
            3,
            2,
          ],
          'circle-stroke-color': [
            'case',
            ['boolean', ['get', 'late'], false],
            '#dc2626',
            ['boolean', ['get', 'priority'], false],
            '#f59e0b',
            '#ffffff',
          ],
          'circle-opacity': ['case', ['boolean', ['feature-state', 'dimmed'], false], 0.25, 1],
        },
      });
      map.addLayer({
        id: LAYER_STOP_LABELS,
        type: 'symbol',
        source: SRC_STOPS,
        filter: ['!', ['has', 'point_count']],
        minzoom: 12,
        layout: {
          'text-field': ['to-string', ['get', 'sequence']],
          'text-size': 10,
          'text-allow-overlap': true,
          'text-offset': [0, 0.05],
        },
        paint: { 'text-color': '#ffffff', 'text-halo-color': 'rgba(0,0,0,0.35)', 'text-halo-width': 0.6 },
      });

      map.addLayer({
        id: LAYER_DEPOTS,
        type: 'circle',
        source: SRC_DEPOTS,
        paint: {
          'circle-color': '#0f172a',
          'circle-radius': 8,
          'circle-stroke-width': 3,
          'circle-stroke-color': '#facc15',
        },
      });

      setStyleReady(true);
      setMapReady(true);
    });

    return () => {
      popupRef.current?.remove();
      map.remove();
      mapRef.current = null;
      setStyleReady(false);
      setMapReady(false);
    };
    // Sources are seeded once on load; subsequent updates go through setData.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- interaction -------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleReady) return;

    const onStopClick = (event: maplibregl.MapMouseEvent) => {
      const features = map.queryRenderedFeatures(event.point, { layers: [LAYER_STOP_POINTS] });
      const feature = features[0];
      if (!feature) return;
      const props = feature.properties as { stop_id: string; route_id: string };
      selectStop(props.stop_id, props.route_id);
    };

    const onRouteClick = (event: maplibregl.MapMouseEvent) => {
      const stops = map.queryRenderedFeatures(event.point, { layers: [LAYER_STOP_POINTS] });
      if (stops.length) return;
      const features = map.queryRenderedFeatures(event.point, { layers: [LAYER_ROUTE_LINE] });
      const feature = features[0];
      if (!feature) return;
      selectRoute((feature.properties as { route_id: string }).route_id);
    };

    const onClusterClick = (event: maplibregl.MapMouseEvent) => {
      const features = map.queryRenderedFeatures(event.point, { layers: [LAYER_STOP_CLUSTERS] });
      const feature = features[0];
      if (!feature) return;
      const source = map.getSource(SRC_STOPS) as GeoJSONSource;
      const clusterId = (feature.properties as { cluster_id: number }).cluster_id;
      void source.getClusterExpansionZoom(clusterId).then((zoom) => {
        map.easeTo({
          center: (feature.geometry as GeoJSON.Point).coordinates as [number, number],
          zoom,
        });
      });
    };

    const onBackgroundClick = (event: maplibregl.MapMouseEvent) => {
      const hits = map.queryRenderedFeatures(event.point, {
        layers: [LAYER_STOP_POINTS, LAYER_ROUTE_LINE, LAYER_STOP_CLUSTERS, LAYER_DEPOTS],
      });
      if (hits.length === 0) selectRoute(null);
    };

    const onStopEnter = (event: maplibregl.MapMouseEvent) => {
      map.getCanvas().style.cursor = 'pointer';
      const feature = map.queryRenderedFeatures(event.point, { layers: [LAYER_STOP_POINTS] })[0];
      if (!feature) return;
      const props = feature.properties as {
        sequence: number;
        address: string;
        eta: string;
        late: boolean;
        order_count: number;
      };
      popupRef.current?.remove();
      popupRef.current = new maplibregl.Popup({
        closeButton: false,
        offset: 14,
        className: 'roe-stop-popup',
      })
        .setLngLat((feature.geometry as GeoJSON.Point).coordinates as [number, number])
        .setHTML(
          `<div class="px-3 py-2 text-xs">
             <div class="font-semibold">Stop ${props.sequence}</div>
             <div class="mt-0.5 max-w-[220px] text-muted-foreground">${escapeHtml(props.address)}</div>
             <div class="mt-1 font-medium ${props.late ? 'text-destructive' : ''}">
               ETA ${formatTime(props.eta)}${props.late ? ' · late' : ''}
             </div>
             <div class="text-muted-foreground">${props.order_count} order(s)</div>
           </div>`,
        )
        .addTo(map);
    };

    const onStopLeave = () => {
      map.getCanvas().style.cursor = '';
      popupRef.current?.remove();
      popupRef.current = null;
    };

    const onRouteEnter = () => {
      map.getCanvas().style.cursor = 'pointer';
    };

    map.on('click', LAYER_STOP_POINTS, onStopClick);
    map.on('click', LAYER_ROUTE_LINE, onRouteClick);
    map.on('click', LAYER_STOP_CLUSTERS, onClusterClick);
    map.on('click', onBackgroundClick);
    map.on('mousemove', LAYER_STOP_POINTS, onStopEnter);
    map.on('mouseleave', LAYER_STOP_POINTS, onStopLeave);
    map.on('mouseenter', LAYER_ROUTE_LINE, onRouteEnter);
    map.on('mouseleave', LAYER_ROUTE_LINE, onStopLeave);
    map.on('mouseenter', LAYER_STOP_CLUSTERS, onRouteEnter);
    map.on('mouseleave', LAYER_STOP_CLUSTERS, onStopLeave);

    return () => {
      map.off('click', LAYER_STOP_POINTS, onStopClick);
      map.off('click', LAYER_ROUTE_LINE, onRouteClick);
      map.off('click', LAYER_STOP_CLUSTERS, onClusterClick);
      map.off('click', onBackgroundClick);
      map.off('mousemove', LAYER_STOP_POINTS, onStopEnter);
      map.off('mouseleave', LAYER_STOP_POINTS, onStopLeave);
      map.off('mouseenter', LAYER_ROUTE_LINE, onRouteEnter);
      map.off('mouseleave', LAYER_ROUTE_LINE, onStopLeave);
      map.off('mouseenter', LAYER_STOP_CLUSTERS, onRouteEnter);
      map.off('mouseleave', LAYER_STOP_CLUSTERS, onStopLeave);
    };
  }, [styleReady, selectRoute, selectStop]);

  // ---- data updates (Requirement 8.6: within 5 s of any change) ----------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleReady) return;
    (map.getSource(SRC_ROUTES) as GeoJSONSource | undefined)?.setData(data.lines);
    (map.getSource(SRC_STOPS) as GeoJSONSource | undefined)?.setData(data.stops);
    (map.getSource(SRC_DEPOTS) as GeoJSONSource | undefined)?.setData(data.depots);
  }, [data, styleReady]);

  // Interaction handlers are attached per layer, so they need the layers to
  // exist; `styleReady` is set at the end of the map's `load` handler.

  useEffect(() => {
    const map = mapRef.current;
    // The layer only exists once the style has finished loading, and a style
    // reload can remove it, so its presence is checked rather than assumed.
    if (!map || !styleReady || !map.getLayer(LAYER_STOP_LABELS)) return;
    map.setLayoutProperty(LAYER_STOP_LABELS, 'visibility', showLabels ? 'visible' : 'none');
  }, [showLabels, styleReady]);

  // Highlight the selected route and dim the rest (Requirement 8.4).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleReady || !map.getSource(SRC_ROUTES) || !map.getSource(SRC_STOPS)) return;

    for (const feature of data.lines.features) {
      const id = feature.properties.route_id;
      map.setFeatureState(
        { source: SRC_ROUTES, id: featureId(id) },
        { selected: id === selectedRouteId, dimmed: Boolean(selectedRouteId) && id !== selectedRouteId },
      );
    }
    for (const feature of data.stops.features) {
      const props = feature.properties;
      map.setFeatureState(
        { source: SRC_STOPS, id: featureId(props.stop_id) },
        {
          selected: props.stop_id === selectedStopId,
          dimmed: Boolean(selectedRouteId) && props.route_id !== selectedRouteId,
        },
      );
    }
  }, [selectedRouteId, selectedStopId, data, styleReady]);

  const fitAll = useCallback(() => {
    const map = mapRef.current;
    if (!map || !data.bounds) return;
    map.fitBounds(data.bounds, { padding: 60, maxZoom: 14, duration: 600 });
  }, [data.bounds]);

  // Fit once the first routes arrive.
  const fittedRef = useRef(false);
  useEffect(() => {
    if (fittedRef.current || !styleReady || !data.bounds) return;
    fittedRef.current = true;
    fitAll();
  }, [styleReady, data.bounds, fitAll]);

  // Zoom to the selected route.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleReady || !selectedRouteId) return;
    const route = routes.find((r) => r.route_id === selectedRouteId);
    if (!route) return;
    const bounds = boundsOf([route]);
    if (bounds) map.fitBounds(bounds, { padding: 90, maxZoom: 14, duration: 500 });
  }, [selectedRouteId, routes, styleReady]);

  if (isError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-muted/30 p-8 text-center">
        <div className="rounded-full bg-destructive/10 p-3">
          <AlertTriangle className="h-6 w-6 text-destructive" />
        </div>
        <div>
          <p className="text-sm font-medium">Map data could not be loaded</p>
          <p className="mt-1 max-w-sm text-xs text-muted-foreground">
            The route data behind the map is unavailable. Check your connection, then refresh.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh map
        </Button>
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      <div
        ref={containerRef}
        data-testid="map-canvas"
        className="h-full w-full"
        role="application"
        aria-label="Route map"
      />

      {mapError && (
        <div className="absolute inset-x-4 top-4 z-10 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div className="flex-1">
            <p className="font-medium">The map could not start</p>
            <p className="text-muted-foreground">{mapError}</p>
          </div>
          <Button variant="outline" size="sm" onClick={onRetry}>
            Retry
          </Button>
        </div>
      )}

      {isLoading && (
        <div className="absolute left-1/2 top-4 z-10 -translate-x-1/2 rounded-full border border-border bg-card/95 px-3 py-1.5 text-xs shadow-sm backdrop-blur">
          Loading routes…
        </div>
      )}

      {!isLoading && routes.length === 0 && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
          <div className="pointer-events-auto max-w-sm rounded-lg border border-border bg-card/95 p-5 text-center shadow-lg backdrop-blur">
            <MapIcon className="mx-auto h-6 w-6 text-muted-foreground" />
            <p className="mt-2 text-sm font-medium">No routes on the map yet</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Add orders and vehicles, then run optimisation to plan the day.
            </p>
          </div>
        </div>
      )}

      <div className="absolute bottom-4 right-4 z-10 flex flex-col gap-2">
        <Button
          variant="outline"
          size="icon"
          onClick={fitAll}
          title="Fit all routes"
          aria-label="Fit all routes in view"
          className="bg-card shadow-sm"
        >
          <Crosshair className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="icon"
          onClick={() => setShowLabels((v) => !v)}
          title={showLabels ? 'Hide stop numbers' : 'Show stop numbers'}
          aria-label={showLabels ? 'Hide stop numbers' : 'Show stop numbers'}
          aria-pressed={showLabels}
          className="bg-card shadow-sm"
        >
          <Layers className="h-4 w-4" />
        </Button>
      </div>

      {!hasBasemap && (
        <div className="pointer-events-none absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-full bg-card/85 px-3 py-1 text-[11px] text-muted-foreground shadow-sm backdrop-blur">
          Offline basemap — set VITE_MAP_TILE_URL for street tiles
        </div>
      )}
    </div>
  );
}


function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
