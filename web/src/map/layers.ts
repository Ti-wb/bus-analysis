import type { ExpressionSpecification } from 'maplibre-gl';
import type { LayerProps } from 'react-map-gl/maplibre';
import { COLORS, COVERAGE_OPACITY, ELDERLY_COLORS, GRID_OPACITY, NO_SCHEDULE, NO_STOP, NO_STOP_COLOR, NO_STOP_OPACITY, PLACE_BY_CATEGORY, VILLAGE_COLORS } from '../config';
import type { BusFocusState } from '../focus/model';

/** Style layers only; react-map-gl's LayerProps also admits custom WebGL layers. */
export type StyleLayer = Exclude<LayerProps, { type: 'custom' }>;

/** Analysis owns colour; transport and facilities provide quiet, selectable context. */
export function analysisContextLayer(layer: StyleLayer): StyleLayer {
  const id = layer.id;
  if (layer.type === 'line' && ['bus-routes', 'regional-bus-routes', 'mrt-lines'].includes(id ?? '')) {
    return { ...layer, paint: { ...layer.paint, 'line-color': '#788894',
      'line-opacity': id === 'mrt-lines' ? .5 : ['interpolate', ['linear'], ['zoom'], 10, .025, 13, .08, 16, .22],
      'line-width': id === 'mrt-lines' ? ['interpolate', ['linear'], ['zoom'], 10, 1, 15, 2.5] : ['interpolate', ['linear'], ['zoom'], 10, .5, 15, 1.2] } };
  }
  if (layer.type === 'circle' && ['bus-stops', 'regional-bus-stops', 'mrt-stations', 'youbike', 'places'].includes(id ?? '')) {
    const metro = id === 'mrt-stations';
    const place = id === 'places';
    return { ...layer, minzoom: metro ? 11 : place || id === 'youbike' ? 14 : 12.5,
      paint: { ...layer.paint, 'circle-color': place || id === 'youbike' ? '#9aa7af' : '#ffffff',
        'circle-stroke-color': '#718491',
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, metro ? 2.5 : 1, 14, metro ? 4 : 2.5, 17, metro ? 6 : 5],
        'circle-stroke-width': metro ? 1.3 : .9,
        'circle-opacity': ['interpolate', ['linear'], ['zoom'], 11, .4, 15, .85],
        'circle-stroke-opacity': ['interpolate', ['linear'], ['zoom'], 11, .4, 15, .85] } };
  }
  if (id === 'mrt-lines-casing' && layer.type === 'line') {
    return { ...layer, paint: { ...layer.paint, 'line-width': 3, 'line-opacity': .5 } };
  }
  return layer;
}

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
    'fill-color': ['match', ['get', 'k'], NO_STOP, NO_STOP_COLOR, 0, COLORS[0], 1, COLORS[1], 2, COLORS[2], 3, COLORS[3], 4, COLORS[4], COLORS[5]],
    'fill-opacity': ['case', ['==', ['get', 'k'], NO_STOP], NO_STOP_OPACITY, GRID_OPACITY],
    'fill-outline-color': 'rgba(255,255,255,0.4)',
  },
};

export const gridHatchLayer: StyleLayer = {
  id: 'grid-hatch',
  type: 'fill',
  filter: ['==', ['get', 'k'], NO_SCHEDULE],
  paint: { 'fill-pattern': 'hatch' },
};

// Transport-need grid: colour comes from need.ts; cells outside the analysis scope are a faint grey.
export const needFillLayer: StyleLayer = {
  id: 'need-fill',
  type: 'fill',
  paint: {
    'fill-color': ['case', ['==', ['get', 'scope'], 1], ['get', 'color'], '#f4f4f2'],
    'fill-opacity': ['case', ['==', ['get', 'scope'], 1], 0.8, 0.45],
    'fill-outline-color': 'rgba(255,255,255,0.6)',
  },
};

export const needPartialLayer: StyleLayer = {
  id: 'need-partial',
  type: 'fill',
  filter: ['all', ['==', ['get', 'scope'], 1], ['==', ['get', 'partial'], 1]],
  paint: { 'fill-pattern': 'hatch-line' },
};

export const needRobustLayer: StyleLayer = {
  id: 'need-robust',
  type: 'line',
  filter: ['==', ['get', 'robust'], 1],
  paint: { 'line-color': '#7a2a0c', 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 1.2, 15, 2.4] },
};

// One pixel per cell; nearest resampling keeps the cells square when zoomed in.
export const coverageLayer: StyleLayer = {
  id: 'coverage',
  type: 'raster',
  paint: { 'raster-resampling': 'nearest', 'raster-fade-duration': 0, 'raster-opacity': COVERAGE_OPACITY },
};

/** White cell edges over the coverage raster; they fade in once a cell is about 10 px wide. */
export function coverageGridLayer(cellM: number): StyleLayer {
  const from = 14 - Math.log2(cellM / 50);
  return {
    id: 'coverage-grid',
    type: 'line',
    paint: {
      'line-color': '#ffffff',
      'line-width': ['interpolate', ['linear'], ['zoom'], from, 0.5, 17, 1.2],
      'line-opacity': ['interpolate', ['linear'], ['zoom'], from - 1, 0, from, 0.75],
    },
  };
}

// Fixed 350 m catchment around the selected station, independent of the coverage settings.
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

/** Ordinary route/station popups use source IDs, including in the route editor. */
export function busRouteHighlightLayer(routeIds: readonly string[] | null): StyleLayer {
  const filter: ExpressionSpecification = routeIds === null
    ? ['boolean', false]
    : ['in', ['get', 'route_id'], ['literal', routeIds]];
  return {
    id: 'bus-route-highlight',
    type: 'line',
    filter,
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: { 'line-color': '#1f8a80', 'line-width': 4 },
  };
}

/** Focus colours and visibility share the exact same route IDs as the sidebar. */
export function busFocusRouteLayers(focus: BusFocusState): StyleLayer[] {
  const routes = focus.routes.filter((route) => route.visible && route.has_geometry);
  const filter: ExpressionSpecification = ['in', ['get', 'route_id'], ['literal', routes.map((route) => route.route_id)]];
  const emphasis = focus.hovered_route_id ?? focus.selected_route_id;
  const color: ExpressionSpecification | string = routes.length
    ? ['match', ['get', 'route_id'], ...routes.flatMap((route) => [route.route_id, route.color]), '#98a6ad'] as unknown as ExpressionSpecification
    : '#98a6ad';
  const layout = { 'line-join': 'round', 'line-cap': 'round' } as const;
  return [
    {
      id: 'bus-focus-routes-casing', type: 'line', filter, layout,
      paint: { 'line-color': '#ffffff', 'line-opacity': .8, 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 3.3, 15, 5.5] },
    },
    {
      id: 'bus-focus-routes', type: 'line', filter, layout,
      paint: {
        'line-color': color,
        'line-opacity': emphasis ? ['case', ['==', ['get', 'route_id'], emphasis], .95, .6] : .85,
        'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1.6, 15, 3],
      },
    },
    {
      id: 'bus-focus-route-emphasis', type: 'line', layout,
      filter: ['all', filter, ['==', ['get', 'route_id'], emphasis ?? '']],
      paint: { 'line-color': color, 'line-opacity': .95, 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 3, 15, 5] },
    },
  ];
}

export const focusBusStopsLayer: StyleLayer = {
  id: 'focus-bus-stops',
  type: 'symbol',
  layout: {
    'icon-image': ['case', ['get', 'focus_served'], 'focus-stop-solid', 'focus-stop-hollow'],
    'icon-size': ['interpolate', ['linear'], ['zoom'], 10, .65, 15, 1],
    'icon-allow-overlap': true,
    'icon-ignore-placement': true,
  },
  paint: { 'icon-opacity': .95 },
};

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
    'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 4.5, 15, 7],
    'circle-color': '#ffffff',
    'circle-stroke-color': '#032c68',
    'circle-stroke-width': 2,
  },
};

export const busStopsLayer: StyleLayer = {
  id: 'bus-stops',
  type: 'circle',
  minzoom: 11,
  paint: {
    'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 1.6, 14, 4, 17, ['interpolate', ['linear'], ['get', 'route_count'], 1, 5, 30, 8]],
    'circle-color': '#ffffff',
    'circle-stroke-color': '#1d559f',
    'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 11, 1, 14, 1.8, 17, 2],
  },
};

export function regionalRoutesLayer(leapfrogOnly: boolean): StyleLayer {
  return {
    id: 'regional-bus-routes', type: 'line',
    filter: leapfrogOnly ? ['==', ['get', 'is_leapfrog'], true] : ['boolean', true],
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: {
      'line-color': ['case', ['==', ['get', 'is_leapfrog'], true], '#a64a85', '#1d559f'],
      'line-width': ['interpolate', ['linear'], ['zoom'], 9, 0.7, 15, 2.5],
      'line-opacity': ['interpolate', ['linear'], ['zoom'], 9, 0.18, 14, 0.48],
      'line-dasharray': [4, 1],
    },
  };
}

export function regionalStopsLayer(leapfrogOnly: boolean): StyleLayer {
  return {
    id: 'regional-bus-stops', type: 'circle', minzoom: busStopsLayer.minzoom,
    filter: leapfrogOnly ? ['==', ['get', 'is_leapfrog'], true] : ['boolean', true],
    paint: {
      ...busStopsLayer.paint,
      'circle-stroke-color': ['case', ['==', ['get', 'is_leapfrog'], true], '#a64a85', '#1d559f'],
    },
  };
}

export function villagesFillLayer(metric: 'population' | 'elderly_share'): StyleLayer {
  const colors = metric === 'population' ? VILLAGE_COLORS : ELDERLY_COLORS;
  const breaks = metric === 'population' ? [2000, 4000, 8000, 12000] : [0.15, 0.2, 0.25, 0.3];
  return {
    id: 'villages-fill', type: 'fill',
    paint: {
      'fill-color': ['case', ['==', ['get', metric], null], '#b9c1c8', ['step', ['get', metric], colors[0], ...breaks.flatMap((breakpoint, i) => [breakpoint, colors[i + 1]])]] as ExpressionSpecification,
      'fill-opacity': 0.62,
    },
  };
}

export const villagesLineLayer: StyleLayer = {
  id: 'villages-line', type: 'line',
  paint: { 'line-color': '#74838c', 'line-opacity': 0.45, 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 0.4, 15, 1.1] },
};

export const trafficSectionsLayer: StyleLayer = {
  id: 'traffic-sections', type: 'line',
  layout: { 'line-join': 'round', 'line-cap': 'round' },
  paint: {
    'line-color': ['case', ['==', ['get', 'travel_speed'], null], '#8996a4', ['step', ['get', 'travel_speed'], '#c34e42', 15, '#e59043', 30, '#bcb751', 50, '#398f81']],
    'line-width': ['interpolate', ['linear'], ['zoom'], 9, 2, 15, 5],
    'line-opacity': 0.85, 'line-dasharray': [3, 1],
  },
};

export const busTravelTimesLayer: StyleLayer = {
  id: 'bus-travel-times', type: 'line',
  layout: { 'line-join': 'round', 'line-cap': 'round' },
  paint: {
    'line-color': ['case', ['==', ['get', 'seconds'], null], '#8996a4', ['step', ['get', 'seconds'], '#71b6b1', 30, '#3c9da4', 60, '#327caf', 120, '#625ca8', 240, '#963c87']],
    'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1.5, 15, 4],
    'line-opacity': 0.9, 'line-dasharray': [2, 1],
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
// Keep facilities secondary to transit stops, including the largest housing sites.
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
        10, ['case', ['has', 'planned_households'], housingRadius(1.5, 2.5), 1.5],
        15, ['case', ['has', 'planned_households'], housingRadius(3, 4), 3]],
      'circle-opacity': .8,
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 10, 0.6, 15, 1],
    },
  };
}


export const villageAgeFillLayer: StyleLayer = {
  id: 'village-age-fill', type: 'fill',
  paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.76, 'fill-outline-color': 'rgba(255,255,255,0.8)' },
};
export const villageAgeOutlineLayer: Extract<StyleLayer, { type: 'line' }> = {
  id: 'village-age-selected', type: 'line',
  paint: { 'line-color': '#032c68', 'line-width': 3 },
};
