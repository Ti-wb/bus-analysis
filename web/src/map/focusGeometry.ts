import type { FeatureCollection, Point } from 'geojson';
import { circle, metresBetween } from '../catchment';
import { lineCoordinates } from '../focus/model';
import { STATION_RADIUS } from '../stations';
import type { BusStopProps, MapData, MrtStationProps, PlaceProps, StationSelection, YouBikeProps } from '../types';

export type FocusBounds = [[number, number], [number, number]];
type BusStation = Extract<StationSelection, { type: 'bus' }>;

/** Include the selected stop, even when source route geometry stops short of it. */
export function focusRouteBounds(data: MapData, ids: readonly string[], at: [number, number]): FocusBounds | null {
  const wanted = new Set(ids);
  const positions = data.bus_routes.features
    .filter((feature) => wanted.has(String(feature.properties?.route_id ?? '')))
    .flatMap((feature) => lineCoordinates(feature.geometry).flat())
    .filter(([lon, lat]) => Number.isFinite(lon) && Number.isFinite(lat) && Math.abs(lon) <= 180 && Math.abs(lat) <= 90);
  if (!positions.length) return null;
  positions.push(at);
  return positions.reduce<FocusBounds>((bounds, [lon, lat]) => [
    [Math.min(bounds[0][0], lon), Math.min(bounds[0][1], lat)],
    [Math.max(bounds[1][0], lon), Math.max(bounds[1][1], lat)],
  ], [[Infinity, Infinity], [-Infinity, -Infinity]]);
}

export function focusLocalBounds(at: [number, number]): FocusBounds {
  const points = circle(at, STATION_RADIUS).geometry.coordinates[0];
  return [
    [Math.min(...points.map(([lon]) => lon)), Math.min(...points.map(([, lat]) => lat))],
    [Math.max(...points.map(([lon]) => lon)), Math.max(...points.map(([, lat]) => lat))],
  ];
}

export interface FocusStops {
  bus: FeatureCollection<Point, BusStopProps & { focus_nearby: boolean; focus_served: boolean }>;
  mrt: FeatureCollection<Point, MrtStationProps>;
  youbike: FeatureCollection<Point, YouBikeProps>;
  places: FeatureCollection<Point, PlaceProps>;
}

/** A union B: nearby stops plus stops served by visible direct routes; never expand via transfers. */
export function focusStops(data: MapData, station: BusStation, ids: readonly string[]): FocusStops {
  const wanted = new Set(ids);
  const within = (point: number[]) => metresBetween(station.at, point) <= STATION_RADIUS;
  const bus = data.bus_stops.features.flatMap((feature) => {
    const focus_nearby = within(feature.geometry.coordinates);
    const focus_served = (feature.properties.route_ids ?? []).some((id) => wanted.has(id));
    return focus_nearby || focus_served
      ? [{ ...feature, properties: { ...feature.properties, focus_nearby, focus_served } }]
      : [];
  });
  return {
    bus: { type: 'FeatureCollection', features: bus },
    mrt: { type: 'FeatureCollection', features: data.mrt_stations.features.filter((feature) => within(feature.geometry.coordinates)) },
    youbike: { type: 'FeatureCollection', features: data.youbike_stations.features.filter((feature) => within(feature.geometry.coordinates)) },
    places: { type: 'FeatureCollection', features: data.places.features.filter((feature) => within(feature.geometry.coordinates)) },
  };
}
