import type { Feature, FeatureCollection, Polygon } from 'geojson';
import { DEFAULT_NEED_WEIGHTS, GAP_BREAKS, GAP_COLORS, NEED_COLORS, type Quadrant } from './config';
import type { NeedGrid, NeedState, NeedWeights } from './types';

/** Percentile rank in [0, 1] among the given indices; ties share the mean rank, zeros stay 0. */
function rank(values: Float64Array, indices: number[]): Float64Array {
  const result = new Float64Array(values.length).fill(NaN);
  const order = [...indices].sort((a, b) => values[a] - values[b]);
  const last = Math.max(order.length - 1, 1);
  for (let start = 0; start < order.length;) {
    let end = start;
    while (end + 1 < order.length && values[order[end + 1]] === values[order[start]]) end++;
    const r = (start + end) / 2 / last;
    for (let k = start; k <= end; k++) result[order[k]] = values[order[k]] === 0 ? 0 : r;
    start = end + 1;
  }
  return result;
}

export interface NeedResult {
  /** Demand rank, supply rank and gap per cell; NaN outside the analysis scope. */
  rDemand: Float64Array;
  rSupply: Float64Array;
  gap: Float64Array;
}

export const isDefaultWeights = (w: NeedWeights) => w.every((v, i) => v === DEFAULT_NEED_WEIGHTS[i]);

/** Demand = weighted sum of the population, place and metro ranks, re-ranked within scope. */
export function computeNeed(grid: NeedGrid, state: Pick<NeedState, 'band' | 'weights'>): NeedResult {
  const v = grid.values[state.band];
  const { in_scope: scope, r_pop: rPop } = grid.cells;
  const n = scope.length;
  const total = state.weights.reduce((a, b) => a + b, 0) || 1;
  const [a, b, c] = state.weights.map((w) => w / total);
  const demand = new Float64Array(n);
  const supply = new Float64Array(n);
  const inside: number[] = [];
  for (let i = 0; i < n; i++) {
    if (!scope[i]) continue;
    inside.push(i);
    demand[i] = a * rPop[i]! + b * v.r_poi[i]! + c * v.r_rail[i]!;
    supply[i] = v.r_supply[i]!;
  }
  const rDemand = rank(demand, inside);
  const rSupply = new Float64Array(n).fill(NaN);
  const gap = new Float64Array(n).fill(NaN);
  for (const i of inside) {
    rSupply[i] = supply[i];
    gap[i] = rDemand[i] - supply[i];
  }
  return { rDemand, rSupply, gap };
}

export function quadrantOf(d: number, s: number): Quadrant | null {
  if (Number.isNaN(d)) return null;
  if (d >= 0.5) return s < 0.5 ? 'priority' : 'saturated';
  return s >= 0.5 ? 'oversupplied' : 'low_low';
}

const tercile = (r: number) => (r < 1 / 3 ? 0 : r < 2 / 3 ? 1 : 2);

export function gapColor(gap: number): string {
  let k = 0;
  while (k < GAP_BREAKS.length && gap >= GAP_BREAKS[k]) k++;
  return GAP_COLORS[k];
}

export interface NeedCellProps {
  i: number;
  /** Fill colour; empty for cells outside the analysis scope. */
  color: string;
  scope: 0 | 1;
  partial: 0 | 1;
  robust: 0 | 1;
}

/** Cell polygons coloured by the bivariate class or the gap; robust marks only apply to default weights. */
export function needCollection(grid: NeedGrid, result: NeedResult, state: NeedState): FeatureCollection<Polygon, NeedCellProps> {
  const [ox, oy] = grid.origin;
  const [sx, sy] = grid.step;
  const robust = grid.values[state.band].robust;
  const showRobust = isDefaultWeights(state.weights);
  const features: Feature<Polygon, NeedCellProps>[] = grid.cells.ix.map((ix, i) => {
    const x0 = ox + ix * sx;
    const y0 = oy + grid.cells.iy[i] * sy;
    const scope = grid.cells.in_scope[i];
    const color = !scope ? ''
      : state.needView === 'gap' ? gapColor(result.gap[i])
        : NEED_COLORS[tercile(result.rDemand[i])][tercile(result.rSupply[i])];
    return {
      type: 'Feature',
      id: i,
      properties: { i, color, scope, partial: grid.cells.partial[i], robust: showRobust && (robust[i] ?? 0) >= 0.8 ? 1 : 0 },
      geometry: { type: 'Polygon', coordinates: [[[x0, y0], [x0 + sx, y0], [x0 + sx, y0 + sy], [x0, y0 + sy], [x0, y0]]] },
    };
  });
  return { type: 'FeatureCollection', features };
}
