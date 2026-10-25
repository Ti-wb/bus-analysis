import type { FeatureCollection, MultiLineString } from 'geojson';
import { COVERAGE_ALPHA, COVERAGE_BANDS, COVERAGE_LAYERS } from './config';
import type { Coverage, CoverageState, StationType } from './types';

export const distanceText = (metres: number) => (metres >= 1000 ? `${metres / 1000} km` : `${metres} m`);

export interface CoverageImage {
  url: string;
  coordinates: [[number, number], [number, number], [number, number], [number, number]];
}

const decoded = new WeakMap<Coverage, Record<StationType, Uint16Array>>();
function fields(coverage: Coverage): Record<StationType, Uint16Array> {
  let f = decoded.get(coverage);
  if (!f) {
    f = {
      bus: Uint16Array.from(coverage.fields.bus),
      mrt: Uint16Array.from(coverage.fields.mrt),
      youbike: Uint16Array.from(coverage.fields.youbike),
    };
    decoded.set(coverage, f);
  }
  return f;
}

/** Distance band 0–4 within the radius, or -1 when the cell is beyond it. */
function band(units: number, coverage: Coverage, radius: number): number {
  const metres = units * coverage.unit_m;
  // A capped value only says "at least cap", so it never falls inside a radius.
  return metres > radius || units >= coverage.cap ? -1 : Math.min(COVERAGE_BANDS - 1, Math.floor((metres / radius) * COVERAGE_BANDS));
}

/** One pixel per cell. Walking takes precedence in overlaps; no unlabelled mixed hue. */
export function coverageImage(coverage: Coverage, state: Pick<CoverageState, 'coverage' | 'walkTypes' | 'walkRadii' | 'rideRadius'>): CoverageImage {
  const [cols, rows] = coverage.size;
  const f = fields(coverage);
  const walkTypes = [...state.walkTypes].map((t) => ({ field: f[t], radius: state.walkRadii[t] }));
  const walk = state.coverage.has('walk') && walkTypes.length > 0;
  const ride = state.coverage.has('ride');
  const [wr, wg, wb] = COVERAGE_LAYERS.walk.rgb;
  const [rr, rg, rb] = COVERAGE_LAYERS.ride.rgb;

  const canvas = document.createElement('canvas');
  canvas.width = cols;
  canvas.height = rows;
  const ctx = canvas.getContext('2d')!;
  const image = ctx.createImageData(cols, rows);
  const px = image.data;
  for (let i = 0; i < cols * rows; i++) {
    if (f.bus[i] === coverage.outside) continue;
    let ra = 0;
    let wa = 0;
    if (ride) {
      const b = band(f.youbike[i], coverage, state.rideRadius);
      if (b >= 0) ra = COVERAGE_ALPHA[b];
    }
    if (walk) {
      // Each type has its own radius; the cell takes the strongest band among them.
      let best = -1;
      for (const { field, radius } of walkTypes) {
        const b = band(field[i], coverage, radius);
        if (b >= 0 && (best < 0 || b < best)) best = b;
      }
      if (best >= 0) wa = COVERAGE_ALPHA[best];
    }
    const a = wa || ra;
    if (!a) continue;
    const o = i * 4;
    px[o] = wa ? wr : rr;
    px[o + 1] = wa ? wg : rg;
    px[o + 2] = wa ? wb : rb;
    px[o + 3] = a * 255;
  }
  ctx.putImageData(image, 0, 0);

  const { west, north } = coverage;
  const east = west + cols * coverage.step[0];
  const south = north - rows * coverage.step[1];
  return { url: canvas.toDataURL(), coordinates: [[west, north], [east, north], [east, south], [west, south]] };
}

/** Nearest-station distances in metres for the cell under a point, or null outside Taipei. */
export function distancesAt(coverage: Coverage, lng: number, lat: number): { metres: Record<StationType, number>; capped: number } | null {
  const [cols, rows] = coverage.size;
  const col = Math.floor((lng - coverage.west) / coverage.step[0]);
  const row = Math.floor((coverage.north - lat) / coverage.step[1]);
  if (col < 0 || row < 0 || col >= cols || row >= rows) return null;
  const f = fields(coverage);
  const i = row * cols + col;
  if (f.bus[i] === coverage.outside) return null;
  const metres = { bus: f.bus[i] * coverage.unit_m, mrt: f.mrt[i] * coverage.unit_m, youbike: f.youbike[i] * coverage.unit_m };
  return { metres, capped: coverage.cap * coverage.unit_m };
}

/** Cell edges over the whole coverage extent, drawn as thin lines on top of the raster. */
export function coverageGridLines(coverage: Coverage): FeatureCollection<MultiLineString> {
  const [cols, rows] = coverage.size;
  const { west, north } = coverage;
  const [sx, sy] = coverage.step;
  const east = west + cols * sx;
  const south = north - rows * sy;
  const lines: [number, number][][] = [];
  for (let c = 0; c <= cols; c++) lines.push([[west + c * sx, north], [west + c * sx, south]]);
  for (let r = 0; r <= rows; r++) lines.push([[west, north - r * sy], [east, north - r * sy]]);
  return { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'MultiLineString', coordinates: lines } }] };
}
