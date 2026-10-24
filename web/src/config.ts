import type { CellSize, CoverageCell, CoverageLayer, DayType, LayerGroup, NeedBand, NeedWeights, StationType } from './types';

export const ALL_DAY = 24;
export const DAY_LABELS: Record<DayType, string> = { weekday: '平日', saturday: '週六', sunday: '週日' };
export const DAY_COLUMN: Record<DayType, 5 | 6 | 7> = { weekday: 5, saturday: 6, sunday: 7 };
export const COLORS = ['#f1d6cf', '#ecdcb8', '#cfe3d6', '#a6d2c9', '#6fb8ad', '#2f8f86'];

interface Scale {
  breaks: number[];
  labels: string[];
}

// Breaks follow the value quantiles of each cell size, so larger cells get a wider scale.
export const SCALES: Record<CellSize, { hourly: Scale; daily: Scale }> = {
  100: {
    hourly: { breaks: [0, 1, 5, 13, 31, 61], labels: ['0', '1–4', '5–12', '13–30', '31–60', '61+'] },
    daily: { breaks: [0, 1, 41, 161, 401, 801], labels: ['0', '1–40', '41–160', '161–400', '401–800', '801+'] },
  },
  150: {
    hourly: { breaks: [0, 1, 6, 15, 36, 71], labels: ['0', '1–5', '6–14', '15–35', '36–70', '71+'] },
    daily: { breaks: [0, 1, 51, 181, 451, 851], labels: ['0', '1–50', '51–180', '181–450', '451–850', '851+'] },
  },
  250: {
    hourly: { breaks: [0, 1, 6, 16, 41, 81], labels: ['0', '1–5', '6–15', '16–40', '41–80', '81+'] },
    daily: { breaks: [0, 1, 51, 201, 501, 1001], labels: ['0', '1–50', '51–200', '201–500', '501–1k', '1k+'] },
  },
  500: {
    hourly: { breaks: [0, 1, 11, 31, 61, 101], labels: ['0', '1–10', '11–30', '31–60', '61–100', '101+'] },
    daily: { breaks: [0, 1, 101, 301, 701, 1201], labels: ['0', '1–100', '101–300', '301–700', '701–1.2k', '1.2k+'] },
  },
};
export const CELL_SIZES = [100, 150, 250, 500] as const satisfies readonly CellSize[];

// Cell classes beyond the colour scale.
export const NO_STOP = -1;
export const NO_SCHEDULE = -2;

export const LAYER_GROUPS: Record<LayerGroup, string[]> = {
  grid: ['grid-fill', 'grid-hatch'],
  routes: ['bus-routes'],
  stops: ['bus-stops'],
  mrt: ['mrt-lines-casing', 'mrt-lines', 'mrt-stations'],
  places: ['places'],
  youbike: ['youbike'],
  regional_routes: ['regional-bus-routes'],
  regional_stops: ['regional-bus-stops'],
  villages: ['villages-fill', 'villages-line'],
  traffic: ['traffic-sections'],
  bus_times: ['bus-travel-times'],
};
export const DEFAULT_LAYERS: LayerGroup[] = ['grid', 'routes', 'regional_routes', 'regional_stops', 'mrt', 'places'];
export const HOVER_LAYERS = ['places', 'youbike', 'bus-stops', 'regional-bus-stops', 'mrt-stations', 'bus-travel-times', 'traffic-sections', 'regional-bus-routes', 'bus-routes', 'mrt-lines', 'villages-fill', 'grid-fill', 'grid-hatch'];

export const VILLAGE_COLORS = ['#edf4ec', '#c8ddbd', '#9abe96', '#639a70', '#28674c'];
export const ELDERLY_COLORS = ['#f5ecdf', '#ecd3ae', '#ddad7c', '#be7d52', '#8d4938'];

export interface PlaceCategory {
  key: string;
  label: string;
  color: string;
  members: string[];
  on: boolean;
}

// Important-place categories from map-data/traffic-focus. Named landuse areas are unverified
// candidates, so they start hidden.
export const PLACE_CATEGORIES: PlaceCategory[] = [
  { key: 'education', label: '學校', color: '#d9822b', members: ['education'], on: true },
  { key: 'hospital', label: '醫院', color: '#d64545', members: ['hospital'], on: true },
  { key: 'shopping', label: '商場市場', color: '#c2549d', members: ['shopping'], on: true },
  { key: 'social_housing', label: '社會住宅', color: '#8c5a2b', members: ['social_housing', 'ancillary_facility'], on: true },
  { key: 'government', label: '市政司法', color: '#4b5563', members: ['government'], on: true },
  { key: 'transport', label: '轉運設施', color: '#3d5a80', members: ['transport'], on: false },
  { key: 'culture_sports', label: '文教運動', color: '#7b61c9', members: ['culture_sports'], on: false },
  { key: 'recreation', label: '公園遊憩', color: '#4c9a52', members: ['recreation'], on: false },
  { key: 'residential', label: '住宅區候選', color: '#9a8f80', members: ['residential'], on: false },
  { key: 'employment', label: '商業產業候選', color: '#8d6e63', members: ['employment'], on: false },
];
export const PLACE_BY_CATEGORY: Record<string, PlaceCategory> = Object.fromEntries(
  PLACE_CATEGORIES.flatMap((c) => c.members.map((m) => [m, c])),
);

// OpenFreeMap Positron vector base map (no API key). Its label layers are moved above the
// frequency grid and network lines so road and place names stay readable.
export const BASE_STYLE = 'https://tiles.openfreemap.org/styles/positron';
export const DEFAULT_VIEW = { center: [121.545, 25.06] as [number, number], zoom: 11.6 };

// Station coverage (Penpot page 04): walking reach to any station, riding reach from YouBike.
// `markers` is the layer group that draws each station type.
export const STATION_TYPES: { key: StationType; label: string; walkLabel: string; markers: LayerGroup }[] = [
  { key: 'bus', label: '公車', walkLabel: '公車站・步行到站', markers: 'stops' },
  { key: 'mrt', label: '捷運', walkLabel: '捷運站・步行到站', markers: 'mrt' },
  { key: 'youbike', label: 'YouBike', walkLabel: 'YouBike 站・步行到站', markers: 'youbike' },
];
// Radius ranges stay within the 3 km cap of the coverage data.
export const COVERAGE_LAYERS: Record<CoverageLayer, { label: string; min: number; max: number; step: number; radius: number; rgb: [number, number, number] }> = {
  walk: { label: '步行到站', min: 100, max: 1000, step: 50, radius: 350, rgb: [232, 132, 66] },
  ride: { label: 'YouBike 騎乘', min: 500, max: 3000, step: 100, radius: 1000, rgb: [47, 158, 143] },
};
export const COVERAGE_CELLS: { cell: CoverageCell; label: string }[] = [
  { cell: 50, label: '細緻' },
  { cell: 100, label: '標準' },
  { cell: 200, label: '粗略' },
];
export const DEFAULT_COVERAGE_CELL: CoverageCell = 100;
/** Opacity of the five distance bands, from the station outwards. */
export const COVERAGE_ALPHA = [0.6, 0.48, 0.37, 0.27, 0.18];
export const COVERAGE_BANDS = COVERAGE_ALPHA.length;

// Transport-need gap (docs/12): demand vs supply ranks on the 250 m grid.
export const NEED_BANDS: { key: NeedBand; label: string; short: string }[] = [
  { key: 'weekday_am', label: '平日早峰 07–09', short: '早峰' },
  { key: 'weekday_midday', label: '平日離峰 10–16', short: '離峰' },
  { key: 'weekday_pm', label: '平日晚峰 17–19', short: '晚峰' },
  { key: 'weekday_evening', label: '平日晚間 20–23', short: '晚間' },
  { key: 'saturday', label: '週六 10–16', short: '週六' },
  { key: 'sunday', label: '週日 10–16', short: '週日' },
];
export const NEED_WEIGHT_LABELS = ['居住人口', '重要地點', '捷運活動'] as const;
export const DEFAULT_NEED_WEIGHTS: NeedWeights = [50, 30, 20];
/** Bivariate colours: NEED_COLORS[demand tercile][supply tercile], low to high. */
export const NEED_COLORS = [
  ['#e8e8e8', '#b5d3e6', '#6aa9d1'],
  ['#e4c3a8', '#adb3b5', '#5d8eb3'],
  ['#d9875a', '#a5705a', '#52627a'],
];
/** Diverging gap colours from oversupplied (−1) to underserved (+1). */
export const GAP_COLORS = ['#2f6e9e', '#7fb0d3', '#ececec', '#f0a776', '#c2481d'];
export const GAP_BREAKS = [-0.4, -0.15, 0.15, 0.4];
export const QUADRANTS = {
  priority: { label: '優先補強', note: '需求高、供給低' },
  saturated: { label: '已飽和', note: '需求高、供給高' },
  oversupplied: { label: '可能過量', note: '需求低、供給高' },
  low_low: { label: 'DRTS 候選', note: '需求低、供給低' },
} as const;
export type Quadrant = keyof typeof QUADRANTS;
