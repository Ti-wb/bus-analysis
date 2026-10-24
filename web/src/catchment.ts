import type { Feature, Point, Polygon } from 'geojson';
import { PLACE_BY_CATEGORY, PLACE_CATEGORIES } from './config';
import type { MapData } from './types';

// Metres per degree near Taipei; close enough for the few hundred metres of a walking radius.
const M_PER_DEG_LAT = 110_574;
const mPerDegLon = (lat: number) => 111_320 * Math.cos((lat * Math.PI) / 180);

export function metresBetween([lon1, lat1]: number[], [lon2, lat2]: number[]): number {
  const dx = (lon2 - lon1) * mPerDegLon((lat1 + lat2) / 2);
  const dy = (lat2 - lat1) * M_PER_DEG_LAT;
  return Math.hypot(dx, dy);
}

/** Circle of `radius` metres around `centre` as a 64-sided polygon. */
export function circle(centre: [number, number], radius: number): Feature<Polygon> {
  const [lon, lat] = centre;
  const ring = Array.from({ length: 65 }, (_, i) => {
    const a = (i / 64) * 2 * Math.PI;
    return [lon + (radius * Math.cos(a)) / mPerDegLon(lat), lat + (radius * Math.sin(a)) / M_PER_DEG_LAT];
  });
  return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring] } };
}

export interface Nearby {
  mrt: { count: number; nearest: { name: string; metres: number } | null };
  youbike: { count: number; docks: number | null };
  stops: number;
  places: { key: string; label: string; color: string; count: number }[];
  facilities: { id: string; name: string; label: string; metres: number }[];
}

/**
 * Stations and places within `radius` metres of `centre`. `stopId` is left out of the stop count;
 * places count only the categories in `placeKeys` (the place filter).
 */
export function nearby(data: MapData, centre: [number, number], radius: number, stopId: string | null, placeKeys: Set<string>): Nearby {
  const within = <P>(features: Feature<Point, P>[]) =>
    features.map((f) => ({ p: f.properties, m: metresBetween(centre, f.geometry.coordinates) })).filter((x) => x.m <= radius);

  const mrt = within(data.mrt_stations.features).sort((a, b) => a.m - b.m);
  const youbike = within(data.youbike_stations.features);
  const stops = within([...data.bus_stops.features, ...data.regional_bus_stops?.features ?? []]).filter((x) => x.p.id !== stopId);
  const placeCounts = new Map<string, number>();
  const facilities: Nearby['facilities'] = [];
  for (const { p, m } of within(data.places.features).sort((a, b) => a.m - b.m)) {
    const key = PLACE_BY_CATEGORY[p.category]?.key;
    if (key && placeKeys.has(key)) {
      placeCounts.set(key, (placeCounts.get(key) ?? 0) + 1);
      facilities.push({ id: p.id, name: p.name ?? '未命名設施', label: PLACE_BY_CATEGORY[p.category].label, metres: Math.round(m) });
    }
  }
  return {
    mrt: { count: mrt.length, nearest: mrt[0] ? { name: mrt[0].p.name, metres: Math.round(mrt[0].m) } : null },
    youbike: { count: youbike.length, docks: youbike.some((x) => x.p.dock_count === null) ? null : youbike.reduce((sum, x) => sum + x.p.dock_count!, 0) },
    stops: stops.length,
    facilities,
    places: PLACE_CATEGORIES.filter((c) => placeCounts.has(c.key)).map((c) => ({ key: c.key, label: c.label, color: c.color, count: placeCounts.get(c.key)! })),
  };
}
