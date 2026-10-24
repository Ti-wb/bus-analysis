import assert from 'node:assert/strict';
import test from 'node:test';
import { assess, draftChanges, duration, frequency, network, routesWithChanges } from '../src/routeEditor/engine';
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
