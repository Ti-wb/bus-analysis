import assert from 'node:assert/strict';
import test from 'node:test';
import type { Feature, LineString } from 'geojson';
import type { MapData } from '../src/types';
import {
  buildFocusRoutes, initialFocus, overviewFocus, selectFocusRoute, showAllFocusRoutes, toggleFocusRoute,
} from '../src/focus/model';
import type { BusStation } from '../src/focus/model';
import { focusLocalBounds, focusRouteBounds, focusStops } from '../src/map/focusGeometry';

function shape(routeId: string, name: string, departures: number | null): Feature<LineString> {
  return { type: 'Feature', properties: { route_id: routeId, name, weekday_departures: departures },
    geometry: { type: 'LineString', coordinates: [[121.5, 25], [121.6, 25.1]] } };
}

function fixture(ids = ['A', 'B', 'C']) {
  const station: BusStation = { type: 'bus', id: 'station', at: [121.5, 25], properties: {
    id: 'station', name: '測試站', route_ids: ids, route_count: new Set(ids).size,
    route_names_by_id: { A: '共同名稱', B: '共同名稱', C: null }, routes: ['名稱獨立排序，不與 ID 配對'], weekday_departures: null,
  } };
  const data = { bus_routes: { type: 'FeatureCollection', features: [
    shape('A', '舊名稱', 15), shape('A', '舊名稱', 15), shape('B', '共同名稱', 88),
    shape('unrelated', '共同名稱', 999),
  ] } } as unknown as MapData;
  return { station, data };
}

test('same names never merge service identities or select an unrelated route', () => {
  const { data, station } = fixture(['B', 'A', 'A']);
  const routes = buildFocusRoutes(data, station);
  assert.deepEqual(routes.map((r) => r.route_id).sort(), ['A', 'B']);
  assert.deepEqual(routes.map((r) => r.name), ['共同名稱', '共同名稱']);
  assert.equal(routes.find((r) => r.route_id === 'A')?.weekday_departures, 15);
  assert.equal(routes.find((r) => r.route_id === 'B')?.weekday_departures, 88);
});

test('direction shapes repeat one daily total; missing shapes and schedules stay visible as unknown', () => {
  const { data, station } = fixture();
  const routes = buildFocusRoutes(data, station);
  assert.equal(routes.find((r) => r.route_id === 'A')?.weekday_departures, 15);
  const missing = routes.find((r) => r.route_id === 'C')!;
  assert.equal(missing.has_geometry, false);
  assert.equal(missing.weekday_departures, null);
  assert.match(missing.name, /C/);
  data.bus_routes.features.push(shape('C', '來源線形名稱', null));
  const withShape = buildFocusRoutes(data, station).find((r) => r.route_id === 'C')!;
  assert.equal(withShape.name, '來源線形名稱');
  assert.equal(withShape.has_geometry, true);
  assert.equal(withShape.weekday_departures, null);
});

test('stations with more than five services retain every service and stable colors', () => {
  const ids = Array.from({ length: 39 }, (_, i) => String(i));
  const { data, station } = fixture(ids);
  station.properties.route_names_by_id = Object.fromEntries(ids.map((id) => [id, `路線 ${id}`]));
  const forward = buildFocusRoutes(data, station);
  const reverse = buildFocusRoutes(data, { ...station, properties: { ...station.properties, route_ids: [...ids].reverse() } });
  assert.equal(forward.length, 39);
  assert.deepEqual(forward.map((r) => [r.route_id, r.color]), reverse.map((r) => [r.route_id, r.color]));
});

test('overview preserves hidden routes; selecting one reveals it without changing its color', () => {
  const { data, station } = fixture();
  const original = initialFocus(buildFocusRoutes(data, station));
  const hidden = toggleFocusRoute(original, 'A');
  const overview = overviewFocus(hidden, 'routes');
  assert.equal(overview.routes.find((r) => r.route_id === 'A')?.visible, false);
  assert.equal(original.routes.find((r) => r.route_id === 'A')?.visible, true);
  const selected = selectFocusRoute(overview, 'A');
  assert.equal(selected.selected_route_id, 'A');
  assert.equal(selected.routes.find((r) => r.route_id === 'A')?.visible, true);
  assert.equal(selected.routes.find((r) => r.route_id === 'A')?.color, original.routes.find((r) => r.route_id === 'A')?.color);
  assert.ok(selected.camera.revision > overview.camera.revision);
  const local = overviewFocus(selected, 'local');
  assert.equal(local.selected_route_id, null);
  assert.equal(local.camera.scope, 'local');
});

test('hiding the selected line clears selection; show all recovers from hiding every usable line', () => {
  const { data, station } = fixture();
  let focus = selectFocusRoute(initialFocus(buildFocusRoutes(data, station)), 'A');
  focus = toggleFocusRoute(focus, 'A');
  assert.equal(focus.selected_route_id, null);
  focus = toggleFocusRoute(focus, 'B');
  assert.ok(focus.routes.filter((r) => r.has_geometry).every((r) => !r.visible));
  const recovered = showAllFocusRoutes(focus);
  assert.ok(recovered.routes.every((r) => r.visible));
  assert.equal(recovered.camera.scope, 'routes');
});

test('unrenderable and unknown services cannot move the map; a no-service station is empty', () => {
  const { data, station } = fixture();
  const focus = initialFocus(buildFocusRoutes(data, station));
  assert.deepEqual(selectFocusRoute(focus, 'C'), focus);
  assert.deepEqual(toggleFocusRoute(focus, 'C'), focus);
  assert.deepEqual(selectFocusRoute(focus, 'unknown'), focus);
  assert.deepEqual(buildFocusRoutes(data, { ...station, properties: { ...station.properties, route_ids: [], route_count: 0 } }), []);
});

test('map bounds include the selected stop and only exact service IDs; missing geometry has no invalid bounds', () => {
  const { data } = fixture();
  data.bus_routes.features.push({ ...shape('unrelated', '共同名稱', 999),
    geometry: { type: 'LineString', coordinates: [[120, 23], [122, 26]] } });
  assert.deepEqual(focusRouteBounds(data, ['A'], [121.7, 25.2]), [[121.5, 25], [121.7, 25.2]]);
  assert.equal(focusRouteBounds(data, ['C'], [121.5, 25]), null);
  assert.equal(focusRouteBounds(data, [], [121.5, 25]), null);
  const local = focusLocalBounds([121.5, 25]);
  assert.ok(local[0][0] < 121.5 && local[1][0] > 121.5 && local[0][1] < 25 && local[1][1] > 25);
  assert.ok(local.flat().every(Number.isFinite));
});

test('map stops are nearby union direct-service stops, counted once and never expanded through transfers', () => {
  const { data, station } = fixture();
  const point = (id: string, lon: number, routeIds: string[] = []) => ({ type: 'Feature' as const,
    geometry: { type: 'Point' as const, coordinates: [lon, 25] },
    properties: { id, name: id, route_ids: routeIds, route_names_by_id: {}, routes: [], route_count: routeIds.length, weekday_departures: null } });
  data.bus_stops = { type: 'FeatureCollection', features: [
    point('station', 121.5, ['A']), point('nearby', 121.501), point('both', 121.502, ['A']),
    point('direct', 121.51, ['A', 'B']), point('transfer-only', 121.52, ['B']),
  ] };
  data.mrt_stations = { type: 'FeatureCollection', features: [point('near-mrt', 121.501), point('far-mrt', 121.52)] };
  data.youbike_stations = { type: 'FeatureCollection', features: [] };
  data.places = { type: 'FeatureCollection', features: [] };
  const shown = focusStops(data, station, ['A']);
  assert.deepEqual(shown.bus.features.map((f) => f.properties.id), ['station', 'nearby', 'both', 'direct']);
  assert.deepEqual(shown.mrt.features.map((f) => f.properties.id), ['near-mrt']);
  assert.equal(shown.bus.features.find((f) => f.properties.id === 'both')?.properties.focus_nearby, true);
  assert.equal(shown.bus.features.find((f) => f.properties.id === 'both')?.properties.focus_served, true);
  const hidden = focusStops(data, station, []);
  assert.deepEqual(hidden.bus.features.map((f) => f.properties.id), ['station', 'nearby', 'both']);
});
