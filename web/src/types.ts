import type { FeatureCollection, Point } from 'geojson';

export type DayType = 'weekday' | 'saturday' | 'sunday';
export type CellSize = 100 | 150 | 250 | 500;
export type LayerGroup = 'grid' | 'routes' | 'stops' | 'mrt' | 'places' | 'youbike';

/** [ix, iy, stop_count, subroute_count, unscheduled_subroute_count, weekday, saturday, sunday] */
export type GridCell = [number, number, number, number, number, number[] | null, number[] | null, number[] | null];

export interface GridMetadata {
  source: string;
  source_data_time: string;
  built_at: string;
  cell: string;
  value: string;
  limitations: string[];
  status_counts: Record<'ok' | 'partial' | 'no_stop' | 'no_schedule', number>;
  scheduled_subroutes: number;
  sequence_subroutes: number;
}

export interface Grid {
  origin: [number, number];
  step: [number, number];
  cell_m: CellSize;
  fields: string[];
  cells: GridCell[];
  metadata: GridMetadata;
}

export interface BusStopProps {
  id: string;
  name: string;
  route_count: number;
  routes: string[];
  weekday_departures: number | null;
}

export interface BusRouteProps {
  route_id: string;
  name: string;
  direction: string;
  weekday_departures: number | null;
}

export interface PlaceProps {
  id: string;
  name: string | null;
  category: string;
  district: string | null;
  source: string;
  planned_households?: number | null;
  progress?: string | null;
}

export interface YouBikeProps {
  station_id: string;
  name: string | null;
  district: string | null;
  dock_count: number | null;
  is_active: boolean | null;
}

export interface NamedProps {
  name: string;
  city_code?: string;
}

export interface MapData {
  roads: FeatureCollection;
  districts: FeatureCollection;
  taipei_boundary: FeatureCollection;
  bus_routes: FeatureCollection;
  bus_stops: FeatureCollection<Point, BusStopProps>;
  mrt_lines: FeatureCollection;
  mrt_stations: FeatureCollection<Point, NamedProps>;
  district_labels: FeatureCollection<Point, NamedProps>;
  places: FeatureCollection<Point, PlaceProps>;
  youbike_stations: FeatureCollection<Point, YouBikeProps>;
}

export interface ViewState {
  center: [number, number];
  zoom: number;
}

export interface AppState extends CoverageState {
  day: DayType;
  hour: number;
  cell: CellSize;
  layers: Set<LayerGroup>;
  places: Set<string>;
}

export type AnalysisMode = 'frequency' | 'coverage';
export type CoverageLayer = 'walk' | 'ride';
export type StationType = 'bus' | 'mrt' | 'youbike';

/** Distance to the nearest station per 50 m cell; see scripts/build_web_data.py build_coverage. */
export interface Coverage {
  west: number;
  north: number;
  step: [number, number];
  size: [number, number];
  cell_m: number;
  unit_m: number;
  cap: number;
  outside: number;
  fields: Record<StationType, number[]>;
  metadata: { built_at: string; cell: string; value: string; sources: Record<StationType, string>; limitations: string[] };
}

export interface CoverageState {
  mode: AnalysisMode;
  coverage: Set<CoverageLayer>;
  walkTypes: Set<StationType>;
  walkRadius: number;
  rideRadius: number;
}
