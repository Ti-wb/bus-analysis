import type { Feature, FeatureCollection, Polygon } from 'geojson';
import { retryableLoader } from './age';
import { loadCompressedJSON, parseStationAges, parseVillageAges } from './ageLoader';
import { ALL_DAY, DAY_COLUMN, NO_SCHEDULE, NO_STOP, SCALES } from './config';
import type { AppState, Coverage, Grid, GridCell, MapData, NeedGrid, StationCatchments } from './types';

const DATA = `${import.meta.env.BASE_URL}data/`;

async function loadJSON<T>(name: string): Promise<T> {
  const response = await fetch(DATA + name);
  if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

const LAYER_FILES = ['roads', 'districts', 'taipei_boundary', 'bus_routes', 'bus_stops', 'mrt_lines', 'mrt_stations', 'district_labels', 'places', 'youbike_stations'] as const;

export async function loadMapData(): Promise<MapData> {
  const docs = await Promise.all(LAYER_FILES.map((name) => loadJSON<FeatureCollection>(`${name}.geojson`)));
  return Object.fromEntries(LAYER_FILES.map((name, i) => [name, docs[i]])) as unknown as MapData;
}

const grids = new Map<number, Promise<Grid>>();
export function loadGrid(cell: number): Promise<Grid> {
  if (!grids.has(cell)) grids.set(cell, loadJSON<Grid>(`bus_grid_${cell}.json`));
  return grids.get(cell)!;
}

const coverages = new Map<number, Promise<Coverage>>();
export function loadCoverage(cell: number): Promise<Coverage> {
  if (!coverages.has(cell)) coverages.set(cell, loadJSON<Coverage>(`coverage_${cell}.json`));
  return coverages.get(cell)!;
}

let need: Promise<NeedGrid> | null = null;
export function loadNeed(): Promise<NeedGrid> {
  need ??= loadJSON<NeedGrid>('transport_need_250.json');
  return need;
}

let catchments: Promise<StationCatchments> | null = null;
export function loadCatchments(): Promise<StationCatchments> {
  catchments ??= loadJSON<StationCatchments>('station_catchments.json').catch((error: unknown) => {
    catchments = null; // A failed request must be retryable from the station card.
    throw error;
  });
  return catchments;
}

export const loadVillageAges = retryableLoader(() => loadCompressedJSON(DATA + 'village_age.geojson', parseVillageAges));
export const loadStationAges = retryableLoader(() => loadCompressedJSON(DATA + 'station_age_catchments.json', parseStationAges));

export function scaleFor(state: Pick<AppState, 'cell' | 'hour'>) {
  return SCALES[state.cell][state.hour === ALL_DAY ? 'daily' : 'hourly'];
}

export function cellValue(cell: GridCell, state: Pick<AppState, 'day' | 'hour'>): number | null {
  const series = cell[DAY_COLUMN[state.day]];
  if (!series) return null;
  return state.hour === ALL_DAY ? series.reduce((a, b) => a + b, 0) : series[state.hour];
}

function classify(value: number, breaks: number[]): number {
  let k = 0;
  breaks.forEach((b, i) => {
    if (value >= b) k = i;
  });
  return k;
}

/** Cell polygons with a colour class `k` for the current day, hour and scale. */
export function gridCollection(grid: Grid, state: Pick<AppState, 'cell' | 'day' | 'hour'>): FeatureCollection<Polygon, { k: number }> {
  const { breaks } = scaleFor(state);
  const [ox, oy] = grid.origin;
  const [sx, sy] = grid.step;
  const features: Feature<Polygon, { k: number }>[] = grid.cells.map((cell, id) => {
    const x0 = ox + cell[0] * sx;
    const y0 = oy + cell[1] * sy;
    const value = cellValue(cell, state);
    let k: number;
    if (!cell[3]) k = NO_STOP;
    else if (value === null) k = NO_SCHEDULE;
    else k = classify(value, breaks);
    return {
      type: 'Feature',
      id,
      properties: { k },
      geometry: { type: 'Polygon', coordinates: [[[x0, y0], [x0 + sx, y0], [x0 + sx, y0 + sy], [x0, y0 + sy], [x0, y0]]] },
    };
  });
  return { type: 'FeatureCollection', features };
}

export function hourText(hour: number): string {
  if (hour === ALL_DAY) return '全日';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(hour)}:00–${pad(hour + 1)}:00`;
}

export function formatCount(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}
