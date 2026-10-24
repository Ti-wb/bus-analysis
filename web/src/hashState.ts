import { ALL_DAY, CELL_SIZES, COVERAGE_LAYERS, DAY_LABELS, DEFAULT_LAYERS, DEFAULT_NEED_WEIGHTS, DEFAULT_VIEW, LAYER_GROUPS, NEED_BANDS, PLACE_CATEGORIES, STATION_TYPES } from './config';
import type { AnalysisMode, AppState, CellSize, CoverageLayer, DayType, LayerGroup, NeedBand, NeedWeights, StationType, ViewState } from './types';

/** Reads the shareable view from the URL hash, falling back to defaults for anything invalid. */
export function readHash(): AppState & ViewState {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const hour = Number(params.get('h'));
  const cell = Number(params.get('cell'));
  const center = (params.get('c') ?? '').split(',').map(Number);
  const day = params.get('day');
  const list = (key: string) => (params.get(key) ?? '').split(',').filter(Boolean);
  const radius = (key: string, layer: CoverageLayer) => {
    const value = Number(params.get(key));
    return COVERAGE_LAYERS[layer].radii.includes(value) ? value : COVERAGE_LAYERS[layer].radius;
  };
  const mode = params.get('mode');
  const band = params.get('band');
  const weights = list('nw').map(Number);
  return {
    day: day && day in DAY_LABELS ? (day as DayType) : 'weekday',
    hour: params.has('h') && Number.isInteger(hour) && hour >= 0 && hour <= ALL_DAY ? hour : 8,
    cell: (CELL_SIZES as readonly number[]).includes(cell) ? (cell as CellSize) : 500,
    layers: new Set(params.has('layers') ? list('layers').filter((l): l is LayerGroup => l in LAYER_GROUPS) : DEFAULT_LAYERS),
    places: new Set(params.has('places')
      ? list('places').filter((k) => PLACE_CATEGORIES.some((c) => c.key === k))
      : PLACE_CATEGORIES.filter((c) => c.on).map((c) => c.key)),
    mode: mode === 'coverage' || mode === 'need' ? (mode as AnalysisMode) : 'frequency',
    band: NEED_BANDS.some((b) => b.key === band) ? (band as NeedBand) : 'weekday_am',
    needView: params.get('nview') === 'gap' ? 'gap' : 'quadrant',
    weights: weights.length === 3 && weights.every((w) => Number.isInteger(w) && w >= 0 && w <= 100) && weights.some((w) => w > 0)
      ? (weights as NeedWeights)
      : DEFAULT_NEED_WEIGHTS,
    coverage: new Set(params.has('cov') ? list('cov').filter((l): l is CoverageLayer => l in COVERAGE_LAYERS) : ['walk', 'ride']),
    walkTypes: new Set(params.has('st')
      ? list('st').filter((t): t is StationType => STATION_TYPES.some((s) => s.key === t))
      : STATION_TYPES.map((s) => s.key)),
    walkRadius: radius('walk', 'walk'),
    rideRadius: radius('ride', 'ride'),
    center: center.length === 2 && center.every(Number.isFinite) ? (center as [number, number]) : DEFAULT_VIEW.center,
    zoom: Number(params.get('z')) || DEFAULT_VIEW.zoom,
  };
}

export function writeHash(state: AppState, view: ViewState): void {
  const params = new URLSearchParams({
    mode: state.mode,
    day: state.day,
    h: String(state.hour),
    cell: String(state.cell),
    layers: [...state.layers].join(','),
    places: [...state.places].join(','),
  });
  if (state.mode === 'coverage') {
    params.set('cov', [...state.coverage].join(','));
    params.set('st', [...state.walkTypes].join(','));
    params.set('walk', String(state.walkRadius));
    params.set('ride', String(state.rideRadius));
  }
  if (state.mode === 'need') {
    params.set('band', state.band);
    params.set('nview', state.needView);
    params.set('nw', state.weights.join(','));
  }
  params.set('z', view.zoom.toFixed(2));
  params.set('c', `${view.center[0].toFixed(4)},${view.center[1].toFixed(4)}`);
  window.history.replaceState(null, '', `#${params}`);
}
