import type { DayType, MapData } from '../types';

export type AssessmentMap = Pick<MapData, 'places' | 'mrt_stations'>;

export type Coordinate = [number, number];
export type Band = 'am' | 'midday' | 'pm' | 'evening' | 'saturday' | 'sunday' | 'weekday_all' | `${'saturday' | 'sunday'}_${'all' | 'am' | 'midday' | 'pm' | 'evening'}`;
export const BANDS: { id: Band; label: string; day: DayType; start: number; end: number }[] = [
  { id: 'am', label: '平日 07–09', day: 'weekday', start: 7, end: 9 },
  { id: 'midday', label: '平日 09–17', day: 'weekday', start: 9, end: 17 },
  { id: 'pm', label: '平日 17–19', day: 'weekday', start: 17, end: 19 },
  { id: 'evening', label: '平日 19–23', day: 'weekday', start: 19, end: 23 },
  { id: 'saturday', label: '週六 07–23', day: 'saturday', start: 7, end: 23 },
  { id: 'sunday', label: '週日 07–23', day: 'sunday', start: 7, end: 23 },
  { id: 'weekday_all', label: '平日 全日', day: 'weekday', start: 0, end: 24 },
  ...(['saturday', 'sunday'] as const).flatMap((day) => ([
    { period: 'all', label: '全日', start: 0, end: 24 },
    { period: 'am', label: '07–09', start: 7, end: 9 },
    { period: 'midday', label: '09–17', start: 9, end: 17 },
    { period: 'pm', label: '17–19', start: 17, end: 19 },
    { period: 'evening', label: '19–23', start: 19, end: 23 },
  ] as const).map((p) => ({ id: `${day}_${p.period}` as Band, label: `${day === 'saturday' ? '週六' : '週日'} ${p.label}`, day, start: p.start, end: p.end }))),
];

export interface Route {
  id: string;
  route_id: string;
  subroute_id: string;
  name: string;
  branch_name: string;
  direction: string;
  direction_label: string;
  stop_ids: string[];
  path: Coordinate[] | null;
  hourly: Record<DayType, number[] | null>;
  frequencies?: Partial<Record<Band, number>>;
}
export interface ScenarioData {
  schema_version: string;
  metadata: {
    source_data_time: string;
    grid: { origin: Coordinate; step: Coordinate; cell_m: number };
    limitations: string[];
  };
  records: {
    routes: Route[];
    stops: Record<string, { name: string; at: Coordinate }>;
    stop_cells: Record<string, number[]>;
    cells: { ix: number[]; iy: number[]; population: (number | null)[]; population_share: number[]; mrt: number[]; youbike: number[] };
  };
}
export type Changes = Record<string, Route | null>;
export type ActionKind = 'add' | 'edit' | 'delete';
export interface AppliedAction {
  speed: number;
  dwell: number;
  kind: ActionKind;
  name: string;
  band: Band;
  before: Changes;
  after: Changes;
  route: Route;
  original: Route | null;
}
export interface Draft {
  kind: ActionKind;
  route: Route;
  original: Route | null;
  band: Band;
}
export interface Network {
  frequency: number[];
  unknown: number[];
}
export interface FacilityImpact {
  id: string;
  name: string;
  at: Coordinate;
  before: { distance: number | null; frequency: number; unknown: boolean; mrt: boolean };
  after: { distance: number | null; frequency: number; unknown: boolean; mrt: boolean };
  status: 'gain' | 'loss' | 'increase' | 'decrease' | 'same' | 'uncertain' | 'outside';
}
export interface Assessment {
  network: Network;
  before: number;
  after: number;
  beforePossible: number;
  afterPossible: number;
  gained: number;
  lost: number;
  decreased: number;
  uncertain: number;
  missingCells: number;
  facilities: FacilityImpact[];
  increasedCells: number[];
  decreasedCells: number[];
}
export const ACTION_LABELS: Record<ActionKind, string> = { add: '新增路線', edit: '編輯路線', delete: '刪除路線' };
