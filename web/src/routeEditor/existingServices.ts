import { metresBetween } from '../catchment';
import { frequency } from './engine';
import type { Band, Route, ScenarioData } from './types';

export interface AreaMatch {
  selectedStopId: string;
  boardingStopIds: string[];
  direct: boolean;
}

export interface ExistingService {
  route: Route;
  frequency: number | null;
  areaMatches: AreaMatch[];
}

/** Services with a boarding stop within 350 m of at least one selected stop. */
export function buildExistingServices(data: ScenarioData, existingRoutes: Route[], selectedStopIds: string[], band: Band): ExistingService[] {
  const stops = Object.entries(data.records.stops);
  const areas = [...new Set(selectedStopIds)].flatMap((selectedStopId) => {
    const centre = data.records.stops[selectedStopId]?.at;
    if (!centre?.every(Number.isFinite)) return [];
    return [{ selectedStopId, nearbyStopIds: new Set(stops
      .filter(([, stop]) => metresBetween(centre, stop.at) <= 350)
      .map(([id]) => id)) }];
  });
  if (!areas.length) return [];

  const services: ExistingService[] = [];
  // Keep each exact branch/direction once, without combining opposite directions.
  for (const route of new Map(existingRoutes.map((route) => [route.id, route])).values()) {
    const stopIds = [...new Set(route.stop_ids)];
    const areaMatches = areas.flatMap(({ selectedStopId, nearbyStopIds }) => {
      const boardingStopIds = stopIds.filter((id) => nearbyStopIds.has(id));
      return boardingStopIds.length ? [{ selectedStopId, boardingStopIds, direct: boardingStopIds.includes(selectedStopId) }] : [];
    });
    if (!areaMatches.length) continue;
    const value = frequency(route, band);
    services.push({ route, frequency: value !== null && Number.isFinite(value) ? value : null, areaMatches });
  }

  const frequencyRank = (value: number | null) => value === null ? 0 : value > 0 ? 2 : 1;
  return services.sort((a, b) => b.areaMatches.length - a.areaMatches.length
    || frequencyRank(b.frequency) - frequencyRank(a.frequency)
    || (b.frequency ?? 0) - (a.frequency ?? 0)
    || a.route.name.localeCompare(b.route.name, 'zh-Hant', { numeric: true })
    || a.route.branch_name.localeCompare(b.route.branch_name, 'zh-Hant', { numeric: true })
    || a.route.direction.localeCompare(b.route.direction)
    || a.route.id.localeCompare(b.route.id));
}
