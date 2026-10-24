import type { MapGeoJSONFeature } from 'maplibre-gl';
import type { MapData, StationSelection } from './types';

/** Independent of the coverage layer's user-selected walking/cycling radii. */
export const STATION_RADIUS = 350;
export const STATION_LAYERS = ['bus-stops', 'mrt-stations', 'youbike'];
export const STATION_LABELS = { bus: '公車站', mrt: '捷運站', youbike: 'YouBike 站' };

/** Resolve the original feature, avoiding MapLibre's stringified array properties. */
export function stationFromFeature(feature: MapGeoJSONFeature, data: MapData): StationSelection | null {
  const p = feature.properties;
  if (feature.layer.id === 'bus-stops') {
    const f = data.bus_stops.features.find((f) => f.properties.id === p.id);
    if (f) return { type: 'bus', id: f.properties.id, at: f.geometry.coordinates as [number, number], properties: f.properties };
  } else if (feature.layer.id === 'mrt-stations') {
    const f = data.mrt_stations.features.find((f) => f.properties.id === p.id);
    if (f) return { type: 'mrt', id: f.properties.id, at: f.geometry.coordinates as [number, number], properties: f.properties };
  } else if (feature.layer.id === 'youbike') {
    const f = data.youbike_stations.features.find((f) => f.properties.station_id === p.station_id);
    if (f) return { type: 'youbike', id: f.properties.station_id, at: f.geometry.coordinates as [number, number], properties: f.properties };
  }
  return null;
}
