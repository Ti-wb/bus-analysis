import { useId, useMemo } from 'react';
import type { Position } from 'geojson';
import { metresBetween } from '../catchment';
import { STATION_RADIUS } from '../stations';
import type { MapData } from '../types';
import { lineCoordinates, type BusStation, type FocusRoute } from './model';

const WIDTH = 276;
const HEIGHT = 156;
const SCALE = 0.18;

/** Clip every real line segment to the inset, keeping full-route geometry out of the SVG. */
function clippedPath(lines: Position[][], project: (p: Position) => [number, number]): string {
  const paths: string[] = [];
  const code = (x: number, y: number) => (x < 0 ? 1 : x > WIDTH ? 2 : 0) | (y < 0 ? 4 : y > HEIGHT ? 8 : 0);
  for (const line of lines) {
    for (let i = 1; i < line.length; i++) {
      let [x1, y1] = project(line[i - 1]);
      let [x2, y2] = project(line[i]);
      let a = code(x1, y1), b = code(x2, y2);
      while (true) {
        if (!(a | b)) {
          paths.push(`M${x1.toFixed(1)},${y1.toFixed(1)}L${x2.toFixed(1)},${y2.toFixed(1)}`);
          break;
        }
        if (a & b) break;
        const outside = a || b;
        let x: number, y: number;
        if (outside & 8) { x = x1 + (x2 - x1) * (HEIGHT - y1) / (y2 - y1); y = HEIGHT; }
        else if (outside & 4) { x = x1 + (x2 - x1) * -y1 / (y2 - y1); y = 0; }
        else if (outside & 2) { y = y1 + (y2 - y1) * (WIDTH - x1) / (x2 - x1); x = WIDTH; }
        else { y = y1 + (y2 - y1) * -x1 / (x2 - x1); x = 0; }
        if (outside === a) { x1 = x; y1 = y; a = code(x1, y1); }
        else { x2 = x; y2 = y; b = code(x2, y2); }
      }
    }
  }
  return paths.join('');
}

export default function NearbyMap({ data, station, routes, onLocate }: {
  data: MapData;
  station: BusStation;
  routes: FocusRoute[];
  onLocate: () => void;
}) {
  const clip = useId();
  const drawing = useMemo(() => {
    const longitudeScale = 111_320 * Math.cos(station.at[1] * Math.PI / 180);
    const project = (p: Position): [number, number] => [
      WIDTH / 2 + (p[0] - station.at[0]) * longitudeScale * SCALE,
      HEIGHT / 2 - (p[1] - station.at[1]) * 110_574 * SCALE,
    ];
    const roads = clippedPath(data.roads.features.flatMap((f) => lineCoordinates(f.geometry)), project);
    const routePaths = new Map<string, string>();
    for (const routeId of station.properties.route_ids ?? []) {
      routePaths.set(routeId, clippedPath(data.bus_routes.features
        .filter((f) => String(f.properties?.route_id) === routeId)
        .flatMap((f) => lineCoordinates(f.geometry)), project));
    }
    const bus = data.bus_stops.features.filter((f) => f.properties.id !== station.id && metresBetween(station.at, f.geometry.coordinates) <= STATION_RADIUS).map((f) => project(f.geometry.coordinates));
    const mrt = data.mrt_stations.features.filter((f) => metresBetween(station.at, f.geometry.coordinates) <= STATION_RADIUS).map((f) => project(f.geometry.coordinates));
    const bikes = data.youbike_stations.features.filter((f) => metresBetween(station.at, f.geometry.coordinates) <= STATION_RADIUS).map((f) => project(f.geometry.coordinates));
    return { roads, routePaths, bus, mrt, bikes };
    // Visibility changes only affect the rendered strokes, not the geometry projection.
  }, [data, station]);

  return <button className="focus-nearby-map" onClick={onLocate} aria-label={`在主地圖查看${station.properties.name}周邊 350 公尺`}>
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="本站周邊 350 公尺近鄰詳圖，使用實際道路、路線與站點位置">
      <defs><clipPath id={clip}><rect width={WIDTH} height={HEIGHT} rx="8" /></clipPath></defs>
      <g clipPath={`url(#${clip})`}>
        <rect width={WIDTH} height={HEIGHT} fill="#edf2f0" />
        <path d={drawing.roads} fill="none" stroke="#d9e3e4" strokeWidth="7" />
        <path d={drawing.roads} fill="none" stroke="#ffffff" strokeWidth="5" />
        <circle cx={WIDTH / 2} cy={HEIGHT / 2} r={STATION_RADIUS * SCALE} fill="#dce7e9" fillOpacity=".4" stroke="#4b8793" strokeWidth="1.2" strokeDasharray="4 3" />
        {routes.filter((route) => route.visible).map((route) => <path key={route.route_id} d={drawing.routePaths.get(route.route_id)} fill="none" stroke={route.color} strokeWidth="1.7" strokeOpacity=".85" />)}
        {drawing.bus.map(([x, y], i) => <circle key={`bus-${i}`} cx={x} cy={y} r="2.4" fill="white" stroke="#638298" strokeWidth="1.2" />)}
        {drawing.mrt.map(([x, y], i) => <rect key={`mrt-${i}`} x={x - 3} y={y - 3} width="6" height="6" rx="1.5" fill="#8a6fb5" stroke="white" strokeWidth="1" />)}
        {drawing.bikes.map(([x, y], i) => <circle key={`bike-${i}`} cx={x} cy={y} r="2.7" fill="#f2b705" stroke="white" strokeWidth="1" />)}
        <circle cx={WIDTH / 2} cy={HEIGHT / 2} r="8" fill="#fff" />
        <circle cx={WIDTH / 2} cy={HEIGHT / 2} r="5" fill="#337cda" stroke="#fff" strokeWidth="1.5" />
        <rect x={WIDTH / 2 - 15} y={HEIGHT / 2 + 10} width="30" height="17" rx="4" fill="#fff" />
        <text x={WIDTH / 2} y={HEIGHT / 2 + 22} textAnchor="middle" fill="#244a68" fontSize="9" fontWeight="700">本站</text>
        <path d="M13 136v5h36v-5" fill="none" stroke="#577381" strokeWidth="1" />
        <text x="31" y="132" textAnchor="middle" fill="#577381" fontSize="8">200 m</text>
        <path d="m259 21 4-10 4 10-4-2Z" fill="#54717e" /><text x="263" y="31" textAnchor="middle" fill="#54717e" fontSize="8">N</text>
      </g>
    </svg>
    <span className="focus-nearby-caption">350 m 近鄰詳圖 <span aria-hidden="true">↗</span></span>
  </button>;
}
