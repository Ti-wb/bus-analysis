import type { Geometry, Position } from 'geojson';
import type { MapData, StationSelection } from '../types';

export type BusStation = Extract<StationSelection, { type: 'bus' }>;

// The same palette is used by the route list, detail map and main map.
export const FOCUS_COLORS = ['#315f79', '#5f8299', '#7794a5', '#b5c1c9', '#8ea7b7', '#517f92', '#8b9dad', '#446b86'];

export interface FocusRoute {
  route_id: string;
  name: string;
  color: string;
  visible: boolean;
  has_geometry: boolean;
  /** Main-route total, already repeated on each direction's shape; never sum shapes. */
  weekday_departures: number | null;
}

export interface BusFocusState {
  routes: FocusRoute[];
  selected_route_id: string | null;
  hovered_route_id: string | null;
  camera: { scope: 'local' | 'routes'; revision: number };
}

export function lineCoordinates(geometry: Geometry | null): Position[][] {
  if (geometry?.type === 'LineString') return [geometry.coordinates];
  if (geometry?.type === 'MultiLineString') return geometry.coordinates;
  if (geometry?.type === 'GeometryCollection') return geometry.geometries.flatMap(lineCoordinates);
  return [];
}

/** Match service IDs, including services for which a route shape is unavailable. */
export function buildFocusRoutes(data: MapData, station: BusStation): FocusRoute[] {
  const ids = [...new Set(station.properties.route_ids ?? [])];
  const shapes = new Map<string, typeof data.bus_routes.features>();
  for (const feature of data.bus_routes.features) {
    const id = String(feature.properties?.route_id ?? '');
    if (ids.includes(id)) shapes.set(id, [...(shapes.get(id) ?? []), feature]);
  }
  const routes = ids.map((route_id) => {
    const features = shapes.get(route_id) ?? [];
    const sourceName = station.properties.route_names_by_id?.[route_id];
    const shapeName = features.find((f) => typeof f.properties?.name === 'string')?.properties?.name;
    const value = features.find((f) => typeof f.properties?.weekday_departures === 'number')?.properties?.weekday_departures;
    return {
      route_id,
      name: sourceName || shapeName || `路線 ${route_id}`,
      color: '',
      visible: true,
      has_geometry: features.some((f) => lineCoordinates(f.geometry).some((line) => line.length > 1)),
      weekday_departures: typeof value === 'number' && Number.isFinite(value) ? value : null,
    };
  });
  routes.sort((a, b) => a.name.localeCompare(b.name, 'zh-Hant', { numeric: true }) || a.route_id.localeCompare(b.route_id));
  return routes.map((route, i) => ({ ...route, color: FOCUS_COLORS[i % FOCUS_COLORS.length] }));
}

export function initialFocus(routes: FocusRoute[]): BusFocusState {
  return { routes, selected_route_id: null, hovered_route_id: null, camera: { scope: 'routes', revision: 0 } };
}

/** Selecting a hidden route reveals it; routes without geometry cannot change the map view. */
export function selectFocusRoute(focus: BusFocusState, routeId: string): BusFocusState {
  if (!focus.routes.some((route) => route.route_id === routeId && route.has_geometry)) return focus;
  return {
    ...focus,
    routes: focus.routes.map((route) => route.route_id === routeId ? { ...route, visible: true } : route),
    selected_route_id: routeId,
    hovered_route_id: null,
    camera: { scope: 'routes', revision: focus.camera.revision + 1 },
  };
}

export function toggleFocusRoute(focus: BusFocusState, routeId: string): BusFocusState {
  const route = focus.routes.find((item) => item.route_id === routeId);
  if (!route) return focus;
  return {
    ...focus,
    routes: focus.routes.map((item) => item === route ? { ...item, visible: !item.visible } : item),
    selected_route_id: focus.selected_route_id === routeId ? null : focus.selected_route_id,
    hovered_route_id: null,
  };
}

export function overviewFocus(focus: BusFocusState, scope: 'local' | 'routes'): BusFocusState {
  return { ...focus, selected_route_id: null, hovered_route_id: null, camera: { scope, revision: focus.camera.revision + 1 } };
}

export function showAllFocusRoutes(focus: BusFocusState): BusFocusState {
  return overviewFocus({ ...focus, routes: focus.routes.map((route) => ({ ...route, visible: true })) }, 'routes');
}
