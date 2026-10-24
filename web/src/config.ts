import type { CellSize, CoverageLayer, DayType, LayerGroup, StationType } from './types';

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
};
export const DEFAULT_LAYERS: LayerGroup[] = ['grid', 'routes', 'mrt', 'places'];
export const HOVER_LAYERS = ['places', 'youbike', 'bus-stops', 'mrt-stations', 'bus-routes', 'mrt-lines', 'grid-fill', 'grid-hatch'];

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
export const STATION_TYPES: { key: StationType; label: string }[] = [
  { key: 'bus', label: '公車' },
  { key: 'mrt', label: '捷運' },
  { key: 'youbike', label: 'YouBike' },
];
export const COVERAGE_LAYERS: Record<CoverageLayer, { label: string; note: string; radii: number[]; radius: number; rgb: [number, number, number] }> = {
  walk: { label: '步行到站', note: '由每個站點向外擴散', radii: [200, 350, 500], radius: 350, rgb: [232, 132, 66] },
  ride: { label: 'YouBike 騎乘', note: '從 YouBike 站向城市延伸', radii: [500, 1000, 1500], radius: 1000, rgb: [47, 158, 143] },
};
/** Opacity of the five distance bands, from the station outwards. */
export const COVERAGE_ALPHA = [0.6, 0.48, 0.37, 0.27, 0.18];
export const COVERAGE_BANDS = COVERAGE_ALPHA.length;
