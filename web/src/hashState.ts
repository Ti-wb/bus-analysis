import { ALL_DAY, CELL_SIZES, DAY_LABELS, DEFAULT_LAYERS, DEFAULT_VIEW, LAYER_GROUPS, PLACE_CATEGORIES } from './config';
import type { AppState, CellSize, DayType, LayerGroup, ViewState } from './types';

/** Reads the shareable view from the URL hash, falling back to defaults for anything invalid. */
export function readHash(): AppState & ViewState {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const hour = Number(params.get('h'));
  const cell = Number(params.get('cell'));
  const center = (params.get('c') ?? '').split(',').map(Number);
  const day = params.get('day');
  const list = (key: string) => (params.get(key) ?? '').split(',').filter(Boolean);
  return {
    day: day && day in DAY_LABELS ? (day as DayType) : 'weekday',
    hour: params.has('h') && Number.isInteger(hour) && hour >= 0 && hour <= ALL_DAY ? hour : 8,
    cell: (CELL_SIZES as readonly number[]).includes(cell) ? (cell as CellSize) : 500,
    layers: new Set(params.has('layers') ? list('layers').filter((l): l is LayerGroup => l in LAYER_GROUPS) : DEFAULT_LAYERS),
    places: new Set(params.has('places')
      ? list('places').filter((k) => PLACE_CATEGORIES.some((c) => c.key === k))
      : PLACE_CATEGORIES.filter((c) => c.on).map((c) => c.key)),
    center: center.length === 2 && center.every(Number.isFinite) ? (center as [number, number]) : DEFAULT_VIEW.center,
    zoom: Number(params.get('z')) || DEFAULT_VIEW.zoom,
  };
}

export function writeHash(state: AppState, view: ViewState): void {
  const params = new URLSearchParams({
    day: state.day,
    h: String(state.hour),
    cell: String(state.cell),
    layers: [...state.layers].join(','),
    places: [...state.places].join(','),
    z: view.zoom.toFixed(2),
    c: `${view.center[0].toFixed(4)},${view.center[1].toFixed(4)}`,
  });
  window.history.replaceState(null, '', `#${params}`);
}
