import assert from 'node:assert/strict';
import test from 'node:test';
import { decodeRegionalGrid } from '../src/regionalGrid';
import { gridClasses, gridCollection } from '../src/data';
import type { Grid } from '../src/types';

function fixture(cell = 500) {
  return {
    schema_version: 1, dataset_id: `regional_bus_grid_${cell}`,
    metadata: {
      built_at: '2026-10-24T08:00:00+00:00', source_data_time: null,
      grid_definition: { origin: [121.4, 25], step: [0.005, 0.0045], cell_m: cell,
        fields: ['ix', 'iy', 'stop_count', 'subroute_count', 'unscheduled_subroute_count', 'weekday', 'saturday', 'sunday'] },
    },
    records: [[0, 0, 1, 1, 1, [7, ...Array(23).fill(0)], null, Array(24).fill(0)]],
  };
}

test('time classes preserve no-stop, unknown and known-zero cells without changing polygon IDs', () => {
  const grid = decodeRegionalGrid(fixture(100), 100);
  const hourly = (morning: number, evening: number) => Array.from({ length: 24 }, (_, hour) => hour === 8 ? morning : hour === 18 ? evening : 0);
  grid.cells = [
    [0, 0, 0, 0, 0, null, null, null],
    [1, 0, 1, 1, 1, null, null, null],
    [2, 0, 1, 1, 0, hourly(0, 5), null, null],
    [3, 0, 1, 1, 0, hourly(31, 0), null, null],
  ] as Grid['cells'];
  const state = { cell: 100 as const, day: 'weekday' as const, hour: 8 };
  assert.deepEqual([...gridClasses(grid, state)], [-1, -2, 0, 4]);
  assert.deepEqual([...gridClasses(grid, { ...state, hour: 18 })], [-1, -2, 2, 0]);
  assert.deepEqual([...gridClasses(grid, { ...state, hour: 24 })], [-1, -2, 1, 1]);
  assert.deepEqual([...gridClasses(grid, { ...state, day: 'sunday' })], [-1, -2, -2, -2]);
  const before = gridCollection(grid, state), after = gridCollection(grid, { ...state, hour: 18 });
  assert.deepEqual(after.features.map(({ id, geometry }) => ({ id, geometry })), before.features.map(({ id, geometry }) => ({ id, geometry })));
});

test('four dataset sizes unwrap compact records while preserving unknown and known zero', () => {
  for (const cell of [100, 150, 250, 500]) {
    const document = fixture(cell);
    const grid = decodeRegionalGrid(document, cell);
    assert.equal(grid.cell_m, cell);
    assert.deepEqual(grid.origin, document.metadata.grid_definition.origin);
    assert.equal(grid.cells, document.records);
    assert.equal(grid.metadata, document.metadata);
    assert.equal(grid.cells[0][5]?.[0], 7);
    assert.equal(grid.cells[0][6], null);
    assert.deepEqual(grid.cells[0][7], Array(24).fill(0));
  }
});

test('legacy payloads and mismatched schema, dataset or resolution fail visibly', () => {
  const document = fixture();
  assert.throws(() => decodeRegionalGrid({ ...document.metadata.grid_definition, cells: document.records }, 500));
  assert.throws(() => decodeRegionalGrid({ ...document, schema_version: 2 }, 500));
  assert.throws(() => decodeRegionalGrid({ ...document, dataset_id: 'bus_grid_500' }, 500));
  assert.throws(() => decodeRegionalGrid(document, 250));
  assert.throws(() => decodeRegionalGrid({ ...document, records: null }, 500));
});

test('missing grid definition, invalid steps and column drift cannot become map geometry', () => {
  for (const mutate of [
    (d: ReturnType<typeof fixture>) => { d.metadata.grid_definition.step[0] = 0; },
    (d: ReturnType<typeof fixture>) => { d.metadata.grid_definition.origin[1] = Number.NaN; },
    (d: ReturnType<typeof fixture>) => { d.metadata.grid_definition.fields.reverse(); },
  ]) {
    const document = fixture(); mutate(document);
    assert.throws(() => decodeRegionalGrid(document, 500));
  }
  assert.throws(() => decodeRegionalGrid({ ...fixture(), metadata: {} }, 500));
});
