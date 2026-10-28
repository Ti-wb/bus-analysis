import type { Coordinate } from './types';

/** Choose usable screen targets without simplifying or changing the route geometry. */
export function pathHandles(path: Coordinate[], project: (point: Coordinate) => { x: number; y: number }, width: number, height: number, focused: number | null = null, spacing = 24) {
  const buckets = new Map<string, { x: number; y: number }[]>();
  const inside = ({ x, y }: { x: number; y: number }) => Number.isFinite(x) && Number.isFinite(y)
    && x >= -spacing && y >= -spacing && x <= width + spacing && y <= height + spacing;
  const add = (point: { x: number; y: number }, force = false) => {
    const col = Math.floor(point.x / spacing), row = Math.floor(point.y / spacing);
    if (!force) for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
      if (buckets.get(`${col + dx}:${row + dy}`)?.some((other) => Math.hypot(other.x - point.x, other.y - point.y) < spacing)) return false;
    }
    const key = `${col}:${row}`;
    const bucket = buckets.get(key) ?? [];
    bucket.push(point); buckets.set(key, bucket);
    return true;
  };
  const points = path.map(project);
  const vertices = new Set<number>();
  // Preserve endpoint access and the keyboard/drag target even when other points overlap.
  for (const i of [focused, 0, path.length - 1]) {
    if (i !== null && points[i] && !vertices.has(i) && (i === focused || inside(points[i]))) {
      vertices.add(i); add(points[i], true);
    }
  }
  points.forEach((point, i) => { if (!vertices.has(i) && inside(point) && add(point)) vertices.add(i); });
  const midpoints: number[] = [];
  for (let i = 0; i < path.length - 1; i++) {
    const midpoint: Coordinate = [(path[i][0] + path[i + 1][0]) / 2, (path[i][1] + path[i + 1][1]) / 2];
    const point = project(midpoint);
    if (Math.hypot(points[i].x - points[i + 1].x, points[i].y - points[i + 1].y) >= spacing * 2 && inside(point) && add(point)) midpoints.push(i);
  }
  return { vertices: [...vertices].sort((a, b) => a - b), midpoints };
}
