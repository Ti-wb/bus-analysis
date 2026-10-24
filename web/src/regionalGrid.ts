import type { Grid, GridMetadata } from './types';

const FIELDS = ['ix', 'iy', 'stop_count', 'subroute_count', 'unscheduled_subroute_count', 'weekday', 'saturday', 'sunday'];
const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const pair = (value: unknown): value is [number, number] =>
  Array.isArray(value) && value.length === 2 && value.every((n) => typeof n === 'number' && Number.isFinite(n));

/** Adapt the retained dataset envelope to the map's shared in-memory Grid. */
export function decodeRegionalGrid(document: unknown, cell: number): Grid {
  const invalid = () => new Error(`regional_bus_grid_${cell}.json：資料格式不符`);
  if (!object(document) || document.schema_version !== 1 || document.dataset_id !== `regional_bus_grid_${cell}`
    || !object(document.metadata) || !Array.isArray(document.records)) throw invalid();
  const metadata = document.metadata;
  const definition = metadata.grid_definition;
  if (!object(definition) || definition.cell_m !== cell || ![100, 150, 250, 500].includes(cell)
    || !pair(definition.origin) || !pair(definition.step) || definition.step.some((n) => n <= 0)
    || !Array.isArray(definition.fields) || definition.fields.length !== FIELDS.length
    || definition.fields.some((name, i) => name !== FIELDS[i]) || typeof metadata.built_at !== 'string') throw invalid();
  return {
    origin: definition.origin, step: definition.step, cell_m: cell as Grid['cell_m'], fields: [...FIELDS],
    cells: document.records as Grid['cells'], metadata: metadata as unknown as GridMetadata,
  };
}
