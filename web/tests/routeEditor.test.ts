import assert from 'node:assert/strict';
import test from 'node:test';
import { assess, draftChanges, duration, frequency, network, routesWithChanges } from '../src/routeEditor/engine';
import { buildExistingServices } from '../src/routeEditor/existingServices';
import { loadScenario, saveScenario } from '../src/routeEditor/storage';
import type { MapData } from '../src/types';
import type { AppliedAction, Route, ScenarioData } from '../src/routeEditor/types';

function route(id: string, routeId: string, stops: string[], value: number | null): Route {
  return { id, route_id: routeId, subroute_id: id, name: routeId, branch_name: routeId, direction: '0', direction_label: '去程', stop_ids: stops,
    path: null, hourly: { weekday: value === null ? null : Array(24).fill(value), saturday: Array(24).fill(1), sunday: null } };
}
function fixture() {
  const a = route('a:0', 'a', ['s1', 's1b', 's2'], 4);
  const b = route('b:0', 'b', ['s2', 's3'], 2);
  const data: ScenarioData = { schema_version: '1.0', metadata: { source_data_time: 'test', grid: { origin: [121.5, 25], step: [.001, .001], cell_m: 100 }, limitations: [] },
    records: { routes: [a, b], stops: { s1: { name: '一', at: [121.5, 25] }, s1b: { name: '一之二', at: [121.5001, 25] }, s2: { name: '二', at: [121.51, 25] }, s3: { name: '三', at: [121.52, 25] }, s4: { name: '四', at: [121.53, 25] } },
      stop_cells: { s1: [0], s1b: [0], s2: [1], s3: [2], s4: [3] },
      cells: { ix: [0, 1, 2, 3], iy: [0, 0, 0, 0], population: [100, 200, 300, 400], population_share: [1, 1, 1, 1], mrt: [0, 0, 0, 0], youbike: [1, 1, 1, 1] } } };
  const map = { mrt_stations: { features: [] }, places: { features: [{ type: 'Feature', properties: { id: 'school', name: '學校', category: 'education' }, geometry: { type: 'Point', coordinates: [121.51, 25] } }] } } as unknown as MapData;
  return { data, map, a, b };
}

test('overlapping stops and repeated visits count each direction once per cell', () => {
  const { data } = fixture();
  assert.deepEqual(network(data, data.records.routes, 'am').frequency, [4, 6, 2, 0]);
});
test('existing services require a boarding stop within 350 m, including the boundary and exact stop evidence', () => {
  const { data } = fixture();
  data.records.stops = {
    centre: { name: '本站', at: [121.5, 0] },
    near: { name: '鄰近站', at: [121.5, 349 / 110574] },
    boundary: { name: '邊界站', at: [121.5, 350 / 110574] },
    outside: { name: '範圍外', at: [121.5, 351 / 110574] },
  };
  data.records.stop_cells = { centre: [0], near: [0], boundary: [0], outside: [0] };
  const direct = route('direct:0', 'direct', ['centre', 'near', 'boundary', 'outside', 'near'], 4);
  const near = route('near:0', 'near', ['near'], 2);
  const boundary = route('boundary:0', 'boundary', ['boundary'], 1);
  const outside = { ...route('outside:0', 'outside', ['outside', 'missing'], 8), path: [[121.49, 0], [121.51, 0]] as [number, number][] };
  const services = buildExistingServices(data, [direct, near, boundary, outside], ['missing', 'centre'], 'am');
  assert.deepEqual(services.map((s) => s.route.id), ['direct:0', 'near:0', 'boundary:0']);
  assert.deepEqual(services[0].areaMatches, [{ selectedStopId: 'centre', boardingStopIds: ['centre', 'near', 'boundary'], direct: true }]);
  assert.deepEqual(services[1].areaMatches, [{ selectedStopId: 'centre', boardingStopIds: ['near'], direct: false }]);
  assert.deepEqual(services[2].areaMatches, [{ selectedStopId: 'centre', boardingStopIds: ['boundary'], direct: false }]);
});
test('existing services deduplicate overlapping areas and exact variants while preserving opposite directions', () => {
  const { data, a, b } = fixture();
  const inbound = { ...a, id: 'a:1', direction: '1', direction_label: '返程', stop_ids: ['s1b'] };
  const routes = [inbound, a, b, a];
  const services = buildExistingServices(data, routes, ['s1', 's1b', 's1'], 'am');
  assert.deepEqual(services.map((s) => s.route.id), ['a:0', 'a:1']);
  assert.deepEqual(services[0].areaMatches, [
    { selectedStopId: 's1', boardingStopIds: ['s1', 's1b'], direct: true },
    { selectedStopId: 's1b', boardingStopIds: ['s1', 's1b'], direct: true },
  ]);
  assert.deepEqual(services[1].areaMatches, [
    { selectedStopId: 's1', boardingStopIds: ['s1b'], direct: false },
    { selectedStopId: 's1b', boardingStopIds: ['s1b'], direct: true },
  ]);
  assert.deepEqual(buildExistingServices(data, [...routes].reverse(), ['s1', 's1b'], 'am'), services);
});
test('existing service frequencies follow the selected band and supplied scenario, retaining zero and unknown values', () => {
  const { data, a } = fixture();
  const unknown = route('u:0', 'u', ['s1'], null);
  const routes = routesWithChanges(data, { [a.id]: { ...a, frequencies: { am: 0, pm: 6 } }, [unknown.id]: unknown });
  const morning = buildExistingServices(data, routes, ['s1'], 'am');
  assert.deepEqual(morning.map((s) => [s.route.id, s.frequency]), [['a:0', 0], ['u:0', null]]);
  assert.deepEqual(buildExistingServices(data, routes, ['s1'], 'pm').map((s) => [s.route.id, s.frequency]), [['a:0', 6], ['u:0', null]]);
  const beforeRemoval = buildExistingServices(data, routes, ['s1', 's2'], 'am');
  const afterRemoval = buildExistingServices(data, routes, ['s2'], 'am');
  assert.equal(beforeRemoval.find((s) => s.route.id === a.id)?.areaMatches.length, 2);
  assert.deepEqual(afterRemoval.find((s) => s.route.id === a.id)?.areaMatches.map((m) => m.selectedStopId), ['s2']);
  assert.equal(afterRemoval.some((s) => s.route.id === unknown.id), false);
  assert.deepEqual(buildExistingServices(data, routes, [], 'am'), []);
  const moved = routesWithChanges(data, { [a.id]: { ...a, stop_ids: ['s4'] } });
  assert.deepEqual(buildExistingServices(data, moved, ['s1'], 'am'), []);
  assert.deepEqual(buildExistingServices(data, [], ['s1'], 'am'), []);
});
test('deletion preserves alternative service, distinguishes lost reach from reduced frequency, and excludes YouBike', () => {
  const { data, map } = fixture();
  const r = assess(data, map, { 'a:0': null }, 'am');
  assert.equal(r.before, 600); assert.equal(r.after, 500); assert.equal(r.lost, 100); assert.equal(r.decreased, 200);
  assert.equal(r.facilities[0].status, 'decrease'); assert.equal(r.facilities[0].after.frequency, 2);
});
test('editing simultaneously gains and loses population; net is gains minus losses', () => {
  const { data, map, a } = fixture();
  const r = assess(data, map, { [a.id]: { ...a, stop_ids: ['s2', 's4'] } }, 'am');
  assert.equal(r.gained, 400); assert.equal(r.lost, 100); assert.equal(r.after - r.before, r.gained - r.lost);
});
test('MRT retains population after bus deletion without inventing MRT departures', () => {
  const { data, map } = fixture(); data.records.cells.mrt[0] = 1;
  const r = assess(data, map, { 'a:0': null }, 'am');
  assert.equal(r.lost, 0); assert.equal(r.after, 600); assert.equal(r.network.frequency[0], 0);
});
test('missing schedules remain unknown and prevent a false loss when an alternative may exist', () => {
  const { data, map } = fixture(); data.records.routes.push(route('u:0', 'u', ['s1', 's1b'], null));
  const r = assess(data, map, { 'a:0': null }, 'am');
  assert.equal(r.after, 500); assert.equal(r.afterPossible, 600); assert.equal(r.lost, 0); assert.equal(r.uncertain, 100);
  assert.equal(r.network.unknown[0], 1);
  data.records.cells.population[0] = null;
  assert.equal(assess(data, map, {}, 'am').missingCells, 1);
});
test('a departure override affects only its chosen band; deleting a line removes every branch and direction', () => {
  const { data, a, b } = fixture(); const inbound = { ...a, id: 'a:1', direction: '1' };
  data.records.routes.push(inbound);
  const edit = { ...a, frequencies: { am: 0 } };
  assert.equal(frequency(edit, 'am'), 0); assert.equal(frequency(edit, 'pm'), 4); assert.equal(frequency(edit, 'saturday'), 1);
  const changes = draftChanges(data, { [a.id]: edit }, { kind: 'delete', route: a, original: a, band: 'am' });
  assert.deepEqual(routesWithChanges(data, changes), [b]);
});
test('route timing includes dwell and follows edited geometry; invalid empty routes have no estimate', () => {
  const { data, a } = fixture();
  assert.equal(duration({ ...a, stop_ids: [] }, data, 18, 30), null);
  const base = duration(a, data, 18, 0)!;
  assert.ok(duration(a, data, 18, 60)! >= base + 2);
  assert.ok(duration({ ...a, path: [[121.5, 25], [121.5, 25.1], [121.51, 25]] }, data, 18, 30)! > base);
});
test('local persistence validates source version, stop IDs and histories; undo snapshots remain intact', () => {
  const { data, a } = fixture(); const memory = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: (k: string) => memory.get(k) ?? null, setItem: (k: string, v: string) => memory.set(k, v) } });
  const changed = { ...a, name: '新名稱' }; const changes = { [a.id]: changed };
  const action: AppliedAction = { kind: 'edit', name: changed.name, route: changed, original: a, before: {}, after: changes, band: 'am', speed: 18, dwell: 30 };
  assert.equal(saveScenario(data, changes, [action]), true);
  assert.deepEqual(loadScenario(data).changes, changes);
  assert.deepEqual(loadScenario(data).history[0].before, {});
  saveScenario(data, { [a.id]: { ...changed, stop_ids: ['missing', 's1'] } }, [action]);
  assert.deepEqual(loadScenario(data).history, []);
  saveScenario(data, changes, [action]); data.metadata.source_data_time = 'new';
  assert.deepEqual(loadScenario(data).changes, {});
});
