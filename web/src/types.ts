import type { FeatureCollection, LineString, MultiLineString, MultiPolygon, Point, Polygon } from 'geojson';

export type DayType = 'weekday' | 'saturday' | 'sunday';
export type CellSize = 100 | 150 | 250 | 500;
export type LayerGroup = 'grid' | 'routes' | 'stops' | 'mrt' | 'places' | 'youbike' | 'regional_routes' | 'regional_stops' | 'villages' | 'traffic' | 'bus_times';

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
  source_scope?: string;
  is_leapfrog?: boolean;
  source_updated_at?: string | null;
}

export interface RegionalRouteProps {
  id: string;
  route_id: string;
  route_uid: string;
  subroute_uid: string;
  name: string;
  subroute_name: string | null;
  direction: string;
  is_leapfrog: boolean;
  source_scope: string;
  source_updated_at: string | null;
  geometry_kind: string;
  stop_count: number;
}

export interface VillageProps {
  id: string;
  name: string;
  city_code: string;
  city_name: string;
  district_name: string;
  population: number | null;
  households: number | null;
  population_age_0_14: number | null;
  population_age_15_64: number | null;
  population_age_65_plus: number | null;
  elderly_share: number | null;
  period: string | null;
  boundary_period: string | null;
  join_status: string;
}

export interface TrafficSectionProps {
  id: string;
  name: string | null;
  road_name: string | null;
  direction: string | null;
  travel_speed: number | null;
  travel_time: number | null;
  data_collect_time: string | null;
  join_status: string;
  geometry_kind: string;
}

export interface BusTravelTimeProps {
  id: string;
  route_uid: string;
  subroute_uid: string;
  route_name: string;
  direction: string;
  from_stop_id: string;
  to_stop_id: string;
  from_name: string;
  to_name: string;
  weekday: (number | null)[];
  weekend: (number | null)[];
  effective_date: string | null;
  expire_date: string | null;
  source_updated_at: string | null;
  geometry_kind: string;
}

export interface SupplementalRecord {
  id: string;
  title: string;
  file: string;
  feature_count: number;
  source_updated_at: string | null;
  period: unknown;
  status: string;
  limitations: string[];
  sources?: unknown[];
}

export interface SupplementalManifest {
  schema_version: string;
  dataset_id: string;
  metadata: { built_at: string; sources?: unknown; limitations: string[] };
  records: SupplementalRecord[];
}

export interface SupplementalData {
  regional_bus_routes: FeatureCollection<MultiLineString, RegionalRouteProps> | null;
  regional_bus_stops: FeatureCollection<Point, BusStopProps> | null;
  villages: (FeatureCollection<Polygon | MultiPolygon, VillageProps> & { metadata?: { period?: unknown } }) | null;
  traffic_sections: (FeatureCollection<LineString, TrafficSectionProps> & { metadata?: { period?: unknown } }) | null;
  bus_travel_times: FeatureCollection<LineString, BusTravelTimeProps> | null;
  supplemental_manifest: SupplementalManifest | null;
  supplemental_errors: string[];
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

export interface MrtStationProps extends NamedProps {
  /** Native OSM ID, kept separate from bus and YouBike IDs. */
  id: string;
}

export type StationSelection =
  | { type: 'bus'; id: string; at: [number, number]; properties: BusStopProps }
  | { type: 'mrt'; id: string; at: [number, number]; properties: MrtStationProps }
  | { type: 'youbike'; id: string; at: [number, number]; properties: YouBikeProps };

export interface CoreMapData {
  roads: FeatureCollection;
  districts: FeatureCollection;
  taipei_boundary: FeatureCollection;
  bus_routes: FeatureCollection;
  bus_stops: FeatureCollection<Point, BusStopProps>;
  mrt_lines: FeatureCollection;
  mrt_stations: FeatureCollection<Point, MrtStationProps>;
  district_labels: FeatureCollection<Point, NamedProps>;
  places: FeatureCollection<Point, PlaceProps>;
  youbike_stations: FeatureCollection<Point, YouBikeProps>;
}

export interface MapData extends CoreMapData, SupplementalData {}

export interface ViewState {
  center: [number, number];
  zoom: number;
}

export interface AppState extends CoverageState, NeedState {
  ageGroup: string;
  ageSet: 'three_stage' | 'planning';
  day: DayType;
  hour: number;
  cell: CellSize;
  layers: Set<LayerGroup>;
  places: Set<string>;
  /** Base-map place and road names, plus the district labels. */
  labels: boolean;
  leapfrogOnly: boolean;
  villageMetric: 'population' | 'elderly_share';
}

export type AnalysisMode = 'frequency' | 'coverage' | 'need' | 'village_age';
export type CoverageLayer = 'walk' | 'ride';
export type StationType = 'bus' | 'mrt' | 'youbike';
export type CoverageCell = 50 | 100 | 200;

/** Distance to the nearest station per cell; see scripts/build_web_data.py build_coverage. */
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
  /** Station types that count towards walking reach, each with its own radius in metres. */
  walkTypes: Set<StationType>;
  walkRadii: Record<StationType, number>;
  rideRadius: number;
  coverageCell: CoverageCell;
  gridLines: boolean;
}

/**
 * [population, households, data_share, age_groups] per radius; see scripts/build_web_data.py build_catchments.
 * age_groups follows StationCatchments.age_groups and adds up to population; null when unknown.
 */
export type CatchmentValue = [number | null, number | null, number, (number[] | null)?];

export interface StationCatchments {
  radii: number[];
  fields: string[];
  age_groups?: string[];
  records: Record<StationType, Record<string, CatchmentValue[]>>;
  metadata: { built_at: string; population_period: { start: string; end: string }; age_period?: { start: string; end: string }; boundary_vintage: number; value: string; data_share: string; limitations: string[] };
}

export type NeedBand = 'weekday_am' | 'weekday_midday' | 'weekday_pm' | 'weekday_evening' | 'saturday' | 'sunday';
export type NeedView = 'quadrant' | 'gap';
/** Weight percentages for population, places and metro activity. */
export type NeedWeights = [number, number, number];

export interface NeedState {
  band: NeedBand;
  needView: NeedView;
  weights: NeedWeights;
}

interface NeedBandValues {
  poi: number[];
  rail: number[];
  f: number[];
  supply: number[];
  /** Ranks are null for cells outside the analysis scope. */
  r_poi: (number | null)[];
  r_rail: (number | null)[];
  r_supply: (number | null)[];
  robust: (number | null)[];
}

/** Transport-need grid in column form; see scripts/build_web_data.py and build_transport_need.py. */
export interface NeedGrid {
  cell_m: number;
  origin: [number, number];
  step: [number, number];
  bands: { key: NeedBand; label: string }[];
  weights: { population: number; places: number; metro: number };
  cells: {
    ix: number[];
    iy: number[];
    district: (string | null)[];
    population: number[];
    in_scope: (0 | 1)[];
    partial: (0 | 1)[];
    r_pop: (number | null)[];
    s_metro: number[];
  };
  values: Record<NeedBand, NeedBandValues>;
  metadata: { built_at: string; scope: string; population_period: { start: string; end: string }; metro_period: { start: string; end: string }; robust_rule: string; limitations: string[] };
}
