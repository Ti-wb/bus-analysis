import assert from 'node:assert/strict';
import test from 'node:test';
import { decodeRegionalGrid } from '../src/regionalGrid';

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
