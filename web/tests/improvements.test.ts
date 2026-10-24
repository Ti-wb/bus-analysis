import assert from 'node:assert/strict';
import test from 'node:test';
import { csv, loadFilters, matches, rankEntries, sortRows } from '../src/improvements/model';
import { DEFAULTS, type Entry, type Filters } from '../src/improvements/types';
import { BANDS, type Band } from '../src/routeEditor/types';
import { frequency } from '../src/routeEditor/engine';

function entry(id: string, patch: Partial<Entry> = {}): Entry {
  return { id, source_id: id, kind: 'routes', name: id, at: [121.5, 25.05], districts: ['臺北市 中正區'], route_ids: [id], stop_ids: ['1', '2'],
    variant_id: `${id}:0`, shared_routes: 20, geometry_overlap: 70, station_overlap: 80, nearest_mrt: null, nearest_toilet: null,
    hourly: { weekday: Array(24).fill(4), saturday: null, sunday: Array(24).fill(0) }, missing_schedules: { weekday: 0, saturday: 1, sunday: 0 }, is_mock: false, ...patch };
}
test('real missing schedules and geometry remain null; known zero stays zero; synthetic rows are explicitly marked', () => {
  const row = entry('a', { geometry_overlap: null });
  const saturday = rankEntries([row], { ...DEFAULTS, day: 'saturday' })[0];
  assert.equal(saturday.frequency, null); assert.equal(saturday.offpeak, null); assert.equal(saturday.geometry_overlap, null); assert.equal(saturday.partial, true);
  assert.equal(saturday.is_mock, true); assert.equal(row.is_mock, false);
  assert.equal(rankEntries([row], { ...DEFAULTS, day: 'sunday' })[0].frequency, 0);
});
test('thresholds change score and tags without changing the underlying mock observations', () => {
  const row = entry('a', { station_overlap: 80, shared_routes: 1, hourly: { weekday: Array(24).fill(0), saturday: null, sunday: null } });
  const loose = rankEntries([row], { ...DEFAULTS, overlap: 50, passengers: 30 })[0];
  const strict = rankEntries([row], { ...DEFAULTS, overlap: 100, passengers: 1 })[0];
  assert(loose.score > strict.score); assert(loose.problems.includes('metro'));
  assert.equal(loose.passengers, strict.passengers); assert.equal(loose.trip, strict.trip);
  assert.equal(rankEntries([row], { ...DEFAULTS, period: 'midday' })[0].frequency, 0);
});
test('priority is bounded and day/period plus preferences recompute the same stable scenario', () => {
  const rows = [entry('a'), entry('b')];
  const f: Filters = { ...DEFAULTS, weights: [100, 0, 0, 0] };
  const ranked = rankEntries(rows, f);
  assert(ranked.every((r) => r.score >= 0 && r.score <= 100));
  assert.deepEqual(ranked[0].components.slice(1), [0, 0, 0]);
  assert.equal(ranked[0].passengers, rankEntries([...rows].reverse(), f)[1].passengers);
  assert.notEqual(ranked[0].passengers, rankEntries(rows, { ...f, period: 'am' })[0].passengers);
});
test('combined filters use OR problems and AND district/query; missing sort values stay last in both directions', () => {
  const rows = rankEntries([entry('a'), entry('b', { geometry_overlap: null }), entry('c', { geometry_overlap: 10 })], DEFAULTS);
  assert(matches(rows[0], { ...DEFAULTS, query: 'a', district: '臺北市 中正區', problems: ['metro', 'cold'] }));
  assert(!matches(rows[0], { ...DEFAULTS, district: '臺北市 北投區' }));
  assert.deepEqual(sortRows(rows, { ...DEFAULTS, sort: 'geometry' }).map((r) => r.id), ['a', 'c', 'b']);
  assert.deepEqual(sortRows(rows, { ...DEFAULTS, sort: 'geometry', descending: false }).map((r) => r.id), ['c', 'a', 'b']);
});
test('shared filters round trip and reject malformed or unbounded values', () => {
  const f: Filters = { ...DEFAULTS, kind: 'terminals', day: 'sunday', view: 'map', period: 'evening', selected: 'terminal:1', weights: [20, 10, 50, 20], query: '臺北' };
  assert.deepEqual(loadFilters(`#improvement=${encodeURIComponent(JSON.stringify(f))}`), f);
  const invalid = loadFilters(`#improvement=${encodeURIComponent(JSON.stringify({ overlap: -20, passengers: 10000, weights: [0, 0, 0, 0], kind: '__proto__', problems: ['rest', 'rest', '__proto__'] }))}`);
  assert.equal(invalid.overlap, 10); assert.equal(invalid.passengers, 30); assert.deepEqual(invalid.weights, DEFAULTS.weights); assert.equal(invalid.kind, 'routes'); assert.deepEqual(invalid.problems, ['rest']);
  assert.deepEqual(loadFilters('#improvement=null'), DEFAULTS);
});
test('CSV exports all filtered rows, real/mock field flags, nulls and safe quoted names', () => {
  const rows = rankEntries(Array.from({ length: 13 }, (_, i) => entry(String(i), { name: i === 0 ? '=SUM(1,2)' : '忠孝"幹線', geometry_overlap: null })), DEFAULTS);
  const result = csv(rows, DEFAULTS, '2026-10-21');
  assert(result.startsWith('\uFEFF')); assert.equal(result.split('\r\n').length, 14);
  assert(result.includes('"geometry_overlap_is_mock"')); assert(result.includes('"trip_overlap_is_mock"')); assert(result.includes('"is_mock"'));
  assert(result.includes('"\'=SUM(1,2)"')); assert(result.includes('"忠孝""幹線"')); assert(result.includes('"",'));
});
test('all list time choices map to editor bands without dropping the selected day or period', () => {
  for (const day of ['weekday', 'saturday', 'sunday'] as const) for (const period of ['all', 'am', 'midday', 'pm', 'evening'] as const) {
    const id = (day === 'weekday' && period !== 'all' ? period : `${day}_${period}`) as Band;
    const band = BANDS.find((b) => b.id === id)!;
    assert(band); assert.equal(band.day, day); assert.equal(period === 'all' ? band.end - band.start : true, period === 'all' ? 24 : true);
    const r = { id: 'a:0', route_id: 'a', subroute_id: 'a', name: 'a', branch_name: 'a', direction: '0', direction_label: '去程', stop_ids: ['1', '2'], path: null,
      hourly: { weekday: Array(24).fill(4), saturday: Array(24).fill(2), sunday: Array(24).fill(1) } };
    assert.equal(frequency(r, id), day === 'weekday' ? 4 : day === 'saturday' ? 2 : 1);
  }
});
