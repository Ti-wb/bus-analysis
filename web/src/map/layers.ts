import type { ExpressionSpecification } from 'maplibre-gl';
import type { LayerProps } from 'react-map-gl/maplibre';
import { COLORS, NO_SCHEDULE, NO_STOP, PLACE_BY_CATEGORY } from '../config';

/** Style layers only; react-map-gl's LayerProps also admits custom WebGL layers. */
export type StyleLayer = Exclude<LayerProps, { type: 'custom' }>;

export const roadsLayer: StyleLayer = {
  id: 'roads',
  type: 'line',
  paint: {
    'line-color': '#ffffff',
    'line-opacity': 0.85,
    'line-width': ['interpolate', ['linear'], ['zoom'],
      10, ['match', ['get', 'class'], ['motorway', 'trunk'], 1.4, 0.6],
      15, ['match', ['get', 'class'], ['motorway', 'trunk'], 6, 3.5]],
  },
};

export const districtsLayer: StyleLayer = {
  id: 'districts',
  type: 'line',
  paint: { 'line-color': '#9aa3a5', 'line-width': 0.8, 'line-dasharray': [3, 2], 'line-opacity': 0.7 },
};

export const taipeiBoundaryLayer: StyleLayer = {
  id: 'taipei-boundary',
  type: 'line',
  paint: { 'line-color': '#5d6b6e', 'line-width': 1.6, 'line-opacity': 0.7 },
};

export const gridFillLayer: StyleLayer = {
  id: 'grid-fill',
  type: 'fill',
  filter: ['!=', ['get', 'k'], NO_SCHEDULE],
  paint: {
    'fill-color': ['match', ['get', 'k'], NO_STOP, COLORS[0], 0, COLORS[0], 1, COLORS[1], 2, COLORS[2], 3, COLORS[3], 4, COLORS[4], COLORS[5]],
    'fill-opacity': ['case', ['==', ['get', 'k'], NO_STOP], 0.28, 0.82],
    'fill-outline-color': 'rgba(255,255,255,0.7)',
  },
};

export const gridHatchLayer: StyleLayer = {
  id: 'grid-hatch',
  type: 'fill',
  filter: ['==', ['get', 'k'], NO_SCHEDULE],
  paint: { 'fill-pattern': 'hatch' },
};

// One pixel per 50 m cell; nearest resampling keeps the cells square when zoomed in.
export const coverageLayer: StyleLayer = {
  id: 'coverage',
  type: 'raster',
  paint: { 'raster-resampling': 'nearest', 'raster-fade-duration': 0, 'raster-opacity': 0.85 },
};

// Walking catchment around a clicked bus stop; matches the walk colour of the coverage mode.
export const catchmentFillLayer: StyleLayer = {
  id: 'catchment-fill',
  type: 'fill',
  paint: { 'fill-color': '#e88442', 'fill-opacity': 0.14 },
};

export const catchmentLineLayer: StyleLayer = {
  id: 'catchment-line',
  type: 'line',
  paint: { 'line-color': '#d4691f', 'line-width': 2, 'line-dasharray': [3, 2] },
};

export const busRoutesLayer: StyleLayer = {
  id: 'bus-routes',
  type: 'line',
  layout: { 'line-join': 'round', 'line-cap': 'round' },
  paint: {
    'line-color': '#4f6f8f',
    // Thin, translucent lines: stacked routes darken on their own and show where the network piles up.
    'line-opacity': ['interpolate', ['linear'], ['zoom'], 10, 0.08, 13, 0.16, 16, 0.3],
    'line-width': ['interpolate', ['linear'], ['zoom'],
      10, 0.6,
      15, ['interpolate', ['linear'], ['coalesce', ['get', 'weekday_departures'], 0], 0, 1, 300, 3]],
  },
};

/** Highlights one clicked route, or every route serving a clicked stop (matched by route name). */
export function busRouteHighlightLayer(highlight: { routeId: string } | { names: string[] } | null): StyleLayer {
  const filter: ExpressionSpecification = !highlight
    ? ['boolean', false]
    : 'routeId' in highlight
      ? ['==', ['get', 'route_id'], highlight.routeId]
      : ['in', ['get', 'name'], ['literal', highlight.names]];
  return {
    id: 'bus-route-highlight',
    type: 'line',
    filter,
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: { 'line-color': '#1f8a80', 'line-width': 4 },
  };
}

export const mrtCasingLayer: StyleLayer = {
  id: 'mrt-lines-casing',
  type: 'line',
  layout: { 'line-join': 'round', 'line-cap': 'round' },
  paint: { 'line-color': '#ffffff', 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 3, 15, 8] },
};

export const mrtLinesLayer: StyleLayer = {
  id: 'mrt-lines',
  type: 'line',
  layout: { 'line-join': 'round', 'line-cap': 'round' },
  paint: { 'line-color': ['get', 'color'], 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1.6, 15, 5] },
};

export const mrtStationsLayer: StyleLayer = {
  id: 'mrt-stations',
  type: 'circle',
  minzoom: 11,
  paint: {
    'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 2.5, 15, 6],
    'circle-color': '#ffffff',
    'circle-stroke-color': '#3d3f4a',
    'circle-stroke-width': 1.5,
  },
};

export const busStopsLayer: StyleLayer = {
  id: 'bus-stops',
  type: 'circle',
  minzoom: 13.5,
  paint: {
    'circle-radius': ['interpolate', ['linear'], ['zoom'], 13.5, 2, 17, ['interpolate', ['linear'], ['get', 'route_count'], 1, 4, 30, 9]],
    'circle-color': '#ffffff',
    'circle-stroke-color': '#4f6f8f',
    'circle-stroke-width': 1.4,
  },
};

// Matches --youbike / --youbike-edge in style.css; stations paused at snapshot time are grey.
export const youbikeLayer: StyleLayer = {
  id: 'youbike',
  type: 'circle',
  minzoom: 12.5,
  paint: {
    'circle-radius': ['interpolate', ['linear'], ['zoom'], 12.5, 2, 17, ['interpolate', ['linear'], ['coalesce', ['get', 'dock_count'], 0], 10, 4, 60, 8]],
    'circle-color': ['case', ['==', ['get', 'is_active'], false], '#b8b8b8', '#f2b705'],
    'circle-stroke-color': '#8a6d00',
    'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 12.5, 0.6, 16, 1.2],
  },
};

const placeColor =['match', ['get', 'category'], ...Object.entries(PLACE_BY_CATEGORY).flatMap(([m, c]) => [m, c.color]), '#888888'] as unknown as ExpressionSpecification;
// Social housing scales with planned households; other places only with zoom.
const housingRadius = (min: number, max: number): ExpressionSpecification => (
  ['interpolate', ['linear'], ['coalesce', ['get', 'planned_households'], 0], 0, min, 1500, max]
);

export function placesLayer(members: string[]): StyleLayer {
  return {
    id: 'places',
    type: 'circle',
    filter: ['in', ['get', 'category'], ['literal', members]],
    paint: {
      'circle-color': placeColor,
      'circle-radius': ['interpolate', ['linear'], ['zoom'],
        10, ['case', ['has', 'planned_households'], housingRadius(2.5, 6), 2.5],
        15, ['case', ['has', 'planned_households'], housingRadius(6, 14), 6]],
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 10, 0.8, 15, 1.6],
    },
  };
}
