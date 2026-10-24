import type { DayType } from '../types';
import type { Coordinate } from '../routeEditor/types';

export type Kind = 'routes' | 'stops' | 'drts' | 'terminals';
export type Period = 'all' | 'am' | 'midday' | 'pm' | 'evening';
export type Problem = 'metro' | 'stack' | 'cold' | 'rest';
export type Weights = [number, number, number, number];
export type SortKey = 'name' | 'score' | 'problems' | 'geometry' | 'station' | 'trip' | 'shared' | 'frequency' | 'offpeak' | 'passengers' | 'cold' | 'recommendation' | 'mrt' | 'population' | 'routes' | 'toilet' | 'rest' | 'uninterrupted';
export interface Filters {
  kind: Kind; day: DayType; period: Period; district: string; problems: Problem[];
  overlap: number; passengers: number; query: string; showLow: boolean; weights: Weights;
  sort: SortKey; descending: boolean; view: 'list' | 'map'; selected: string | null;
}
export interface PlaceDistance { name: string; distance_m: number }
export interface Entry {
  id: string; source_id: string; kind: Kind; name: string; subtitle?: string;
  at: Coordinate; districts: string[]; route_ids: string[]; stop_ids: string[];
  variant_id: string | null; variant_ids?: string[]; profile_stops?: string[]; profile_direction?: string;
  path?: Coordinate[]; path_is_stop_connection?: boolean; bounds?: [Coordinate, Coordinate];
  geometry_overlap: number | null; geometry_directions?: number; total_directions?: number;
  station_overlap: number; shared_routes: number; nearest_mrt: PlaceDistance | null; nearest_toilet: PlaceDistance | null;
  hourly: Record<DayType, number[] | null>; missing_schedules: Record<DayType, number>;
  population?: number | null; population_missing_cells?: number; terminal_directions?: number; is_mock: false;
}
export interface ImprovementData {
  schema_version: string;
  metadata: { source_data_time: string; counts: Record<Kind, number>; limitations: string[]; mock_fields_generated_in_ui: string[] };
  records: Entry[];
}
export interface Ranked extends Omit<Entry, 'is_mock'> {
  is_mock: true;
  frequency: number | null; peak: number | null; offpeak: number | null; partial: boolean;
  trip: number; passengers: number; coldShare: number; coldPeriod: string; rest: number; uninterrupted: number;
  score: number; components: number[]; problems: Problem[]; recommendation: string;
  affectedTrips: number; releasedHours: number; populationChange: number;
}
export const KINDS: Record<Kind, string> = { routes: '路線', stops: '站位・路段', drts: 'DRTS 區塊', terminals: '端點站' };
export const PROBLEMS: Record<Problem, string> = { metro: '捷運重疊', stack: '路線堆疊', cold: '低載冷區', rest: '休息不足' };
export const PERIODS: Record<Period, { label: string; start: number; end: number }> = {
  all: { label: '全日', start: 0, end: 24 }, am: { label: '早尖峰 07–09', start: 7, end: 9 },
  midday: { label: '離峰 09–17', start: 9, end: 17 }, pm: { label: '晚尖峰 17–19', start: 17, end: 19 },
  evening: { label: '夜間 19–23', start: 19, end: 23 },
};
export const DEFAULTS: Filters = { kind: 'routes', day: 'weekday', period: 'all', district: '', problems: [], overlap: 70,
  passengers: 5, query: '', showLow: false, weights: [35, 25, 25, 15], sort: 'score', descending: true, view: 'list', selected: null };
