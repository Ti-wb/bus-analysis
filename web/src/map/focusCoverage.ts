import type { FeatureCollection, MultiLineString } from 'geojson';
import { COVERAGE_ALPHA, COVERAGE_BANDS, COVERAGE_LAYERS } from '../config';
import type { CoverageImage } from '../coverage';
import { STATION_RADIUS } from '../stations';
import type { CoverageCell, MapData } from '../types';
import type { BusStation } from '../focus/model';

const M_PER_DEG_LAT = 110_574;
const mPerDegLon = (latitude: number) => 111_320 * Math.cos(latitude * Math.PI / 180);

export interface FocusCoverage {
  west: number;
  north: number;
  step: [number, number];
  size: [number, number];
  cell_m: CoverageCell;
  /** Infinity means no included stop is within 350 m of this cell centre. */
  distance_m: Float32Array;
}

/** Recompute from the visible services' own stops, never from the global nearest-station field. */
export function buildFocusCoverage(data: MapData, station: BusStation, routeIds: readonly string[], cellM: CoverageCell): FocusCoverage {
  const wanted = new Set(routeIds);
  const stops: [number, number][] = [station.at];
  for (const feature of data.bus_stops.features) {
    if (feature.properties.id === station.id || !(feature.properties.route_ids ?? []).some((id) => wanted.has(id))) continue;
    const [lon, lat] = feature.geometry.coordinates;
    if (Number.isFinite(lon) && Number.isFinite(lat)) stops.push([lon, lat]);
  }

  // Keep the grid anchored when a route is hidden; cell centres and colour bands must not shift.
  const step: [number, number] = [cellM / mPerDegLon(25.05), cellM / M_PER_DEG_LAT];
  let minLon = station.at[0], maxLon = minLon, minLat = station.at[1], maxLat = minLat;
  for (const [lon, lat] of stops) {
    minLon = Math.min(minLon, lon); maxLon = Math.max(maxLon, lon);
    minLat = Math.min(minLat, lat); maxLat = Math.max(maxLat, lat);
  }
  const latMargin = STATION_RADIUS / M_PER_DEG_LAT;
  const lonMargin = STATION_RADIUS / mPerDegLon(maxLat + latMargin);
  const west = Math.floor((minLon - lonMargin) / step[0]) * step[0];
  const north = Math.ceil((maxLat + latMargin) / step[1]) * step[1];
  const cols = Math.max(1, Math.ceil((maxLon + lonMargin - west) / step[0]));
  const rows = Math.max(1, Math.ceil((north - minLat + latMargin) / step[1]));
  const distances = new Float32Array(cols * rows).fill(Infinity);
  const radiusSquared = STATION_RADIUS ** 2;

  // Each stop touches only its nearby cells (at most about 16 × 16 at 50 m resolution).
  for (const [lon, lat] of stops) {
    const firstCol = Math.max(0, Math.floor((lon - lonMargin - west) / step[0]));
    const lastCol = Math.min(cols - 1, Math.ceil((lon + lonMargin - west) / step[0]));
    const firstRow = Math.max(0, Math.floor((north - lat - latMargin) / step[1]));
    const lastRow = Math.min(rows - 1, Math.ceil((north - lat + latMargin) / step[1]));
    for (let row = firstRow; row <= lastRow; row++) {
      const centreLat = north - (row + .5) * step[1];
      const dy = (centreLat - lat) * M_PER_DEG_LAT;
      if (Math.abs(dy) > STATION_RADIUS) continue;
      const scaleLon = mPerDegLon((centreLat + lat) / 2);
      for (let col = firstCol; col <= lastCol; col++) {
        const dx = (west + (col + .5) * step[0] - lon) * scaleLon;
        const squared = dx * dx + dy * dy;
        const index = row * cols + col;
        if (squared <= radiusSquared && squared < distances[index] ** 2) distances[index] = Math.sqrt(squared);
      }
    }
  }
  return { west, north, step, size: [cols, rows], cell_m: cellM, distance_m: distances };
}

/** Use the ordinary walking palette, but only paint cells reached by the included bus stops. */
export function focusCoverageImage(coverage: FocusCoverage): CoverageImage {
  const [cols, rows] = coverage.size;
  const canvas = document.createElement('canvas');
  canvas.width = cols; canvas.height = rows;
  const context = canvas.getContext('2d')!;
  const image = context.createImageData(cols, rows);
  const [red, green, blue] = COVERAGE_LAYERS.walk.rgb;
  for (let index = 0; index < coverage.distance_m.length; index++) {
    const distance = coverage.distance_m[index];
    if (!Number.isFinite(distance)) continue;
    const band = Math.min(COVERAGE_BANDS - 1, Math.floor(distance / STATION_RADIUS * COVERAGE_BANDS));
    image.data.set([red, green, blue, COVERAGE_ALPHA[band] * 255], index * 4);
  }
  context.putImageData(image, 0, 0);
  const { west, north, step } = coverage;
  const east = west + cols * step[0], south = north - rows * step[1];
  return { url: canvas.toDataURL(), coordinates: [[west, north], [east, north], [east, south], [west, south]] };
}

export function focusDistanceAt(coverage: FocusCoverage, lon: number, lat: number): number | null {
  const [cols, rows] = coverage.size;
  const col = Math.floor((lon - coverage.west) / coverage.step[0]);
  const row = Math.floor((coverage.north - lat) / coverage.step[1]);
  if (col < 0 || row < 0 || col >= cols || row >= rows) return null;
  const distance = coverage.distance_m[row * cols + col];
  return Number.isFinite(distance) ? distance : null;
}

/** Merge adjacent cell edges, omitting every transparent part of the raster extent. */
export function focusCoverageGridLines(coverage: FocusCoverage): FeatureCollection<MultiLineString> {
  const { west, north, step: [sx, sy], size: [cols, rows] } = coverage;
  const active = (col: number, row: number) => col >= 0 && col < cols && row >= 0 && row < rows && Number.isFinite(coverage.distance_m[row * cols + col]);
  const lines: [number, number][][] = [];
  for (let row = 0; row <= rows; row++) {
    let start = -1;
    for (let col = 0; col <= cols; col++) {
      const edge = col < cols && (active(col, row - 1) || active(col, row));
      if (edge && start < 0) start = col;
      if (!edge && start >= 0) { lines.push([[west + start * sx, north - row * sy], [west + col * sx, north - row * sy]]); start = -1; }
    }
  }
  for (let col = 0; col <= cols; col++) {
    let start = -1;
    for (let row = 0; row <= rows; row++) {
      const edge = row < rows && (active(col - 1, row) || active(col, row));
      if (edge && start < 0) start = row;
      if (!edge && start >= 0) { lines.push([[west + col * sx, north - start * sy], [west + col * sx, north - row * sy]]); start = -1; }
    }
  }
  return { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'MultiLineString', coordinates: lines } }] };
}
