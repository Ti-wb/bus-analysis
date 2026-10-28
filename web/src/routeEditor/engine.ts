import type { FeatureCollection, Polygon } from 'geojson';
import { metresBetween } from '../catchment';
import { PLACE_CATEGORIES } from '../config';
import { BANDS, type Assessment, type AssessmentMap, type Band, type Changes, type Draft, type FacilityImpact, type Network, type Route, type ScenarioData } from './types';

export function frequency(route: Route, band: Band): number | null {
  const override = route.frequencies?.[band];
  if (override !== undefined) return override;
  const b = BANDS.find((v) => v.id === band)!;
  const hours = route.hourly[b.day];
  return hours ? hours.slice(b.start, b.end).reduce((sum, n) => sum + n, 0) / (b.end - b.start) : null;
}

export function routesWithChanges(data: ScenarioData, changes: Changes): Route[] {
  const routes = new Map(data.records.routes.map((r) => [r.id, r]));
  for (const [id, route] of Object.entries(changes)) {
    if (route) routes.set(id, route);
    else routes.delete(id);
  }
  return [...routes.values()];
}

export function draftChanges(data: ScenarioData, changes: Changes, draft: Draft): Changes {
  if (draft.kind !== 'delete') return { ...changes, [draft.route.id]: draft.route };
  // Deleting a line removes every branch/direction, including previous edits in this scenario.
  return {
    ...changes,
    ...Object.fromEntries(routesWithChanges(data, changes).filter((r) => r.route_id === draft.route.route_id).map((r) => [r.id, null])),
  };
}

export function routePath(route: Route, data: ScenarioData) {
  return route.path ?? route.stop_ids.map((id) => data.records.stops[id].at);
}

export function duration(route: Route, data: ScenarioData, speed: number, dwell: number): number | null {
  if (new Set(route.stop_ids).size < 2) return null;
  const points = routePath(route, data);
  const metres = points.slice(1).reduce((sum, point, i) => sum + metresBetween(points[i], point), 0);
  return Math.round(metres / (speed * 1000 / 60) + route.stop_ids.length * dwell / 60);
}

export function network(data: ScenarioData, routes: Route[], band: Band): Network {
  const n = data.records.cells.ix.length;
  const result: Network = { frequency: Array(n).fill(0), unknown: Array(n).fill(0) };
  for (const route of routes) {
    const raw = frequency(route, band);
    const f = raw !== null && Number.isFinite(raw) ? raw : null;
    if (f === 0) continue;
    // A route/direction serves a cell once, regardless of overlapping stop circles or repeat visits.
    const cells = new Set(route.stop_ids.flatMap((id) => data.records.stop_cells[id] ?? []));
    for (const i of cells) {
      if (f === null) result.unknown[i]++;
      else result.frequency[i] += f;
    }
  }
  return result;
}

function facilityServices(routes: Route[], band: Band) {
  const memberships = new Map<string, Route[]>();
  for (const r of routes) {
    if (frequency(r, band) === 0) continue;
    for (const id of new Set(r.stop_ids)) memberships.set(id, [...(memberships.get(id) ?? []), r]);
  }
  return memberships;
}

function facilities(data: ScenarioData, map: AssessmentMap, routes: Route[], baseRoutes: Route[], band: Band, changedStops: Set<string>): FacilityImpact[] {
  if (!changedStops.size) return [];
  const changed = [...changedStops].map((id) => data.records.stops[id]?.at).filter((p) => p !== undefined);
  const beforeServices = facilityServices(baseRoutes, band);
  const afterServices = facilityServices(routes, band);
  const stops = Object.entries(data.records.stops);
  const inspect = (at: number[], services: Map<string, Route[]>): FacilityImpact['before'] => {
    let distance = Infinity;
    const nearbyRoutes = new Map<string, Route>();
    for (const [id, stop] of stops) {
      if (!services.has(id)) continue;
      const d = metresBetween(at, stop.at);
      distance = Math.min(distance, d);
      if (d <= 350) for (const r of services.get(id)!) nearbyRoutes.set(r.id, r);
    }
    let mrt = false;
    for (const station of map.mrt_stations.features) {
      const d = metresBetween(at, station.geometry.coordinates);
      distance = Math.min(distance, d);
      if (d <= 350) mrt = true;
    }
    const values = [...nearbyRoutes.values()].map((r) => { const f = frequency(r, band); return f !== null && Number.isFinite(f) ? f : null; });
    return { distance: Number.isFinite(distance) ? distance : null,
      frequency: values.reduce<number>((sum, f) => sum + (f ?? 0), 0), unknown: values.includes(null), mrt };
  };
  const categories = new Set(PLACE_CATEGORIES.filter((c) => c.on).flatMap((c) => c.members));
  return map.places.features.filter((f) => f.properties.name && categories.has(f.properties.category) && changed.some((p) => metresBetween(p, f.geometry.coordinates) <= 1000))
    .map((f): FacilityImpact => {
      const at = f.geometry.coordinates as [number, number];
      const before = inspect(at, beforeServices), after = inspect(at, afterServices);
      const b = before.frequency > 0 || before.mrt;
      const a = after.frequency > 0 || after.mrt;
      const status = (!b && before.unknown) || (!a && after.unknown) ? 'uncertain'
        : !b && a ? 'gain' : b && !a ? 'loss' : !b && !a ? 'outside' : after.frequency > before.frequency + .001 ? 'increase'
        : after.frequency < before.frequency - .001 ? 'decrease' : 'same';
      return { id: f.properties.id, name: f.properties.name!, at, before, after, status };
    }).sort((a, b) => {
      const order = { loss: 0, uncertain: 1, decrease: 2, gain: 3, increase: 4, same: 5, outside: 6 };
      return order[a.status] - order[b.status] || (a.after.distance ?? Infinity) - (b.after.distance ?? Infinity);
    });
}

export function assess(data: ScenarioData, map: AssessmentMap, changes: Changes, band: Band): Assessment {
  const routes = routesWithChanges(data, changes);
  const base = network(data, data.records.routes, band);
  const next = network(data, routes, band);
  const result: Assessment = { network: next, before: 0, after: 0, beforePossible: 0, afterPossible: 0,
    gained: 0, lost: 0, decreased: 0, uncertain: 0, missingCells: 0, facilities: [], increasedCells: [], decreasedCells: [] };
  const { population, mrt } = data.records.cells;
  population.forEach((p, i) => {
    const b = base.frequency[i] > 0 || mrt[i] === 1;
    const a = next.frequency[i] > 0 || mrt[i] === 1;
    const bu = !b && base.unknown[i] > 0, au = !a && next.unknown[i] > 0;
    const change = next.frequency[i] - base.frequency[i];
    if (change > .001 || (!b && a)) result.increasedCells.push(i);
    if (change < -.001 || (b && !a)) result.decreasedCells.push(i);
    if (p === null) { if (a || b || au || bu) result.missingCells++; return; }
    if (b) result.before += p;
    if (a) result.after += p;
    if (b || bu) result.beforePossible += p;
    if (a || au) result.afterPossible += p;
    // Unknown schedules may supply an alternative: never assert a gain/loss there as certain.
    if (!b && !bu && a) result.gained += p;
    if (b && !a && !au) result.lost += p;
    if ((bu || au) && (b !== a || base.unknown[i] !== next.unknown[i])) result.uncertain += p;
    if (b && a && change < -.001) result.decreased += p;
  });
  const changedStops = new Set<string>();
  const originals = new Map(data.records.routes.map((r) => [r.id, r]));
  for (const [id, r] of Object.entries(changes)) {
    originals.get(id)?.stop_ids.forEach((s) => changedStops.add(s));
    r?.stop_ids.forEach((s) => changedStops.add(s));
  }
  result.facilities = facilities(data, map, routes, data.records.routes, band, changedStops);
  return result;
}

export function gridFeatures(data: ScenarioData, result: Assessment): FeatureCollection<Polygon> {
  const { origin: [ox, oy], step: [sx, sy] } = data.metadata.grid;
  const { ix, iy, mrt, youbike } = data.records.cells;
  const increased = new Set(result.increasedCells), decreased = new Set(result.decreasedCells);
  return { type: 'FeatureCollection', features: ix.flatMap((x, i) => {
    const f = result.network.frequency[i], unknown = result.network.unknown[i] > 0;
    if (!(f || unknown || mrt[i] || youbike[i] || increased.has(i) || decreased.has(i))) return [];
    const x0 = ox + x * sx, y0 = oy + iy[i] * sy;
    return [{ type: 'Feature' as const, id: i, properties: {
      frequency: f, unknown, walk: f > 0 || unknown || mrt[i] === 1, mrt: mrt[i] === 1,
      bike: youbike[i] === 1, increased: increased.has(i), decreased: decreased.has(i),
    }, geometry: { type: 'Polygon' as const, coordinates: [[[x0, y0], [x0 + sx, y0], [x0 + sx, y0 + sy], [x0, y0 + sy], [x0, y0]]] } }];
  }) };
}
