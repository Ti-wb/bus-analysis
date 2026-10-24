import { COVERAGE_ALPHA, COVERAGE_BANDS, COVERAGE_LAYERS } from './config';
import type { Coverage, CoverageState, StationType } from './types';

export const distanceText = (metres: number) => (metres >= 1000 ? `${metres / 1000} km` : `${metres} m`);

export interface CoverageImage {
  url: string;
  coordinates: [[number, number], [number, number], [number, number], [number, number]];
}

const decoded = new WeakMap<Coverage, Record<StationType, Uint8Array>>();
function fields(coverage: Coverage): Record<StationType, Uint8Array> {
  let f = decoded.get(coverage);
  if (!f) {
    f = {
      bus: Uint8Array.from(coverage.fields.bus),
      mrt: Uint8Array.from(coverage.fields.mrt),
      youbike: Uint8Array.from(coverage.fields.youbike),
    };
    decoded.set(coverage, f);
  }
  return f;
}

/** Distance band 0–4 within the radius, or -1 when the cell is beyond it. */
function band(units: number, unitM: number, radius: number): number {
  const metres = units * unitM;
  return metres > radius ? -1 : Math.min(COVERAGE_BANDS - 1, Math.floor((metres / radius) * COVERAGE_BANDS));
}

/** Paints one pixel per cell, walking reach composited over riding reach. */
export function coverageImage(coverage: Coverage, state: CoverageState): CoverageImage {
  const [cols, rows] = coverage.size;
  const f = fields(coverage);
  const walkTypes = [...state.walkTypes].map((t) => f[t]);
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
      const b = band(f.youbike[i], coverage.unit_m, state.rideRadius);
      if (b >= 0) ra = COVERAGE_ALPHA[b];
    }
    if (walk) {
      let nearest = coverage.cap;
      for (const t of walkTypes) if (t[i] < nearest) nearest = t[i];
      const b = band(nearest, coverage.unit_m, state.walkRadius);
      if (b >= 0) wa = COVERAGE_ALPHA[b];
    }
    const a = wa + ra * (1 - wa);
    if (!a) continue;
    const o = i * 4;
    px[o] = (wr * wa + rr * ra * (1 - wa)) / a;
    px[o + 1] = (wg * wa + rg * ra * (1 - wa)) / a;
    px[o + 2] = (wb * wa + rb * ra * (1 - wa)) / a;
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
