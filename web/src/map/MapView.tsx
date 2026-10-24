import type { FeatureCollection, MultiLineString, Point, Polygon } from 'geojson';
import type { ExpressionSpecification, MapGeoJSONFeature, Map as MaplibreMap } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
// MapLibre 6 resolves its worker next to its own module, which the bundle does not copy; ship it as an asset.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react';
import Map, {
  AttributionControl,
  Layer,
  Marker,
  NavigationControl,
  Popup,
  Source,
  type LayerProps,
  type MapLayerMouseEvent,
  type MapRef,
} from 'react-map-gl/maplibre';
import { circle } from '../catchment';
import StationInfo, { StationIcon } from '../components/StationInfo';
import { BASE_STYLE, HOVER_LAYERS, LAYER_GROUPS, PLACE_CATEGORIES } from '../config';
import { distancesAt, type CoverageImage } from '../coverage';
import type { BusFocusState } from '../focus/model';
import type { NeedCellProps, NeedResult } from '../need';
import { STATION_LAYERS, STATION_RADIUS, stationFromFeature } from '../stations';
import { CoverageTooltip, FeatureTooltip, NeedTooltip, RoutePopup, type PopupFeature } from './FeatureInfo';
import * as L from './layers';
import { focusLocalBounds, focusRouteBounds, focusStops } from './focusGeometry';
import { RouteLayers, ScenarioGrid, type EditorMapProps } from '../routeEditor/RouteLayers';
import type { AppState, Coverage, Grid, LayerGroup, MapData, NeedGrid, StationSelection, ViewState } from '../types';

const LABEL_NAME: ExpressionSpecification = ['coalesce', ['get', 'name:zh-Hant'], ['get', 'name:nonlatin'], ['get', 'name']];
const LAYER_GROUP_OF: Record<string, LayerGroup> = Object.fromEntries(
  Object.entries(LAYER_GROUPS).flatMap(([group, ids]) => ids.map((id) => [id, group as LayerGroup])),
);

interface Props {
  editor?: EditorMapProps;
  focus?: BusFocusState | null;
  onFocusRoute?: (routeId: string) => void;
  mapRef: RefObject<MapRef | null>;
  initialView: ViewState;
  data: MapData;
  grid: Grid;
  gridData: FeatureCollection<Polygon, { k: number }>;
  coverage: Coverage | null;
  coverageImage: CoverageImage | null;
  coverageLines: FeatureCollection<MultiLineString> | null;
  need: { grid: NeedGrid; result: NeedResult; data: FeatureCollection<Polygon, NeedCellProps> } | null;
  state: AppState;
  station: StationSelection | null;
  onStation: (station: StationSelection | null, origin?: HTMLElement) => void;
  onCloseStation: () => void;
  onReady: () => void;
  onMoveEnd: (view: ViewState) => void;
}

// Distance between the cursor and the tooltip, in pixels.
const TOOLTIP_GAP = 12;
const FOCUS_ORIGINAL_LAYERS: Record<string, string> = {
  'focus-bus-stops': 'bus-stops',
  'focus-mrt-stations': 'mrt-stations',
  'focus-youbike': 'youbike',
  'focus-places': 'places',
  'bus-focus-route-emphasis': 'bus-routes',
  'bus-focus-routes': 'bus-routes',
};

interface Hover {
  feature?: MapGeoJSONFeature;
  /** Nearest-station distances when no feature is under the cursor in coverage mode. */
  distances?: NonNullable<ReturnType<typeof distancesAt>>;
  x: number;
  y: number;
}

function addHatchPattern(map: MaplibreMap) {
  const size = 8;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = 'rgba(245,245,245,0.9)';
  ctx.fillRect(0, 0, size, size);
  ctx.strokeStyle = '#c8c8c8';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(0, size);
  ctx.lineTo(size, 0);
  ctx.stroke();
  map.addImage('hatch', ctx.getImageData(0, 0, size, size));

  // Transparent hatch drawn over coloured cells (transport-need cells with missing schedules).
  ctx.clearRect(0, 0, size, size);
  ctx.strokeStyle = 'rgba(40,40,40,0.55)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, size);
  ctx.lineTo(size, 0);
  ctx.stroke();
  map.addImage('hatch-line', ctx.getImageData(0, 0, size, size));
  ctx.clearRect(0, 0, size, size);
  ctx.strokeStyle = '#be4966';
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(0, size); ctx.lineTo(size, 0); ctx.stroke();
  map.addImage('scenario-hatch', ctx.getImageData(0, 0, size, size));

  // Square stops distinguish the local catchment from stops on the visible routes.
  for (const solid of [false, true]) {
    const icon = document.createElement('canvas');
    icon.width = icon.height = 20;
    const iconContext = icon.getContext('2d')!;
    iconContext.fillStyle = solid ? '#5f8299' : '#ffffff';
    iconContext.strokeStyle = solid ? '#ffffff' : '#315f79';
    iconContext.lineWidth = 3;
    iconContext.fillRect(3, 3, 14, 14);
    iconContext.strokeRect(3, 3, 14, 14);
    map.addImage(`focus-stop-${solid ? 'solid' : 'hollow'}`, iconContext.getImageData(0, 0, 20, 20), { pixelRatio: 2 });
  }
}

export default function MapView({ editor, focus, onFocusRoute, mapRef, initialView, data, grid, gridData, coverage, coverageImage, coverageLines, need, state, station, onStation, onCloseStation, onReady, onMoveEnd }: Props) {
  // First label layer of the base style: our layers go below it so road names stay on top.
  const [labelLayer, setLabelLayer] = useState<string | null>(null);
  const baseLabels = useRef<string[]>([]);
  const [hover, setHover] = useState<Hover | null>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);

  // Keep the tooltip inside the map: place it below-right of the cursor, flipping to the
  // other side when it would cross the right or bottom edge.
  useLayoutEffect(() => {
    const tip = tooltipRef.current;
    const box = mapRef.current?.getContainer();
    if (!hover || !tip || !box) return;
    const fitsRight = hover.x + TOOLTIP_GAP + tip.offsetWidth <= box.clientWidth;
    const fitsBelow = hover.y + TOOLTIP_GAP + tip.offsetHeight <= box.clientHeight;
    const dx = fitsRight ? TOOLTIP_GAP : -TOOLTIP_GAP - tip.offsetWidth;
    const dy = fitsBelow ? TOOLTIP_GAP : -TOOLTIP_GAP - tip.offsetHeight;
    tip.style.transform = `translate(${dx}px, ${dy}px)`;
  }, [hover, mapRef]);
  const [popup, setPopup] = useState<PopupFeature | null>(null);
  const [showDistrictLabels, setShowDistrictLabels] = useState(true);
  const catchmentShape = useMemo(() => (station ? circle(station.at, STATION_RADIUS) : null), [station]);
  const activeFocus = !editor && station?.type === 'bus' ? focus ?? null : null;
  const visibleRouteIds = useMemo(() => activeFocus?.routes.filter((route) => route.visible).map((route) => route.route_id) ?? [], [activeFocus?.routes]);
  const nearbyAndRouteStops = useMemo(() => activeFocus && station?.type === 'bus' ? focusStops(data, station, visibleRouteIds) : null, [activeFocus !== null, data, station, visibleRouteIds]);
  const cameraRequest = useRef<string | null>(null);
  const cameraStation = useRef<StationSelection | null>(null);

  // Camera actions belong to Focus; user layer preferences are never rewritten.
  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!map || labelLayer === null) return;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!activeFocus || station?.type !== 'bus') {
      cameraRequest.current = null;
      cameraStation.current = null;
      return;
    }
    const request = `${station.id}:${activeFocus.camera.scope}:${activeFocus.camera.revision}`;
    const enteringStation = cameraStation.current !== station;
    if (cameraRequest.current === request && !enteringStation) return;
    cameraStation.current = station;
    cameraRequest.current = request;
    const ids = activeFocus.selected_route_id
      ? visibleRouteIds.filter((id) => id === activeFocus.selected_route_id)
      : visibleRouteIds;
    const routeBounds = activeFocus.camera.scope === 'routes' ? focusRouteBounds(data, ids, station.at) : null;
    // All routes hidden: retain the user's current view; a station without shapes starts locally.
    if (activeFocus.camera.scope === 'routes' && !routeBounds && !enteringStation) return;
    const bounds = routeBounds ?? focusLocalBounds(station.at);
    const container = map.getContainer();
    const frame = container.getBoundingClientRect();
    const stage = container.parentElement;
    const sidebar = stage?.querySelector<HTMLElement>('.focus-sidebar')?.getBoundingClientRect();
    const card = stage?.querySelector<HTMLElement>('.station-card.is-focus')?.getBoundingClientRect();
    const mobile = frame.width <= 760;
    const padding = mobile
      ? { top: card ? Math.min(card.bottom - frame.top + 20, frame.height * .3) : 90, right: 30, bottom: sidebar ? Math.min(frame.bottom - sidebar.top + 20, frame.height * .5) : 80, left: 30 }
      : { top: 86, right: card ? card.width + 40 : 40, bottom: 50, left: sidebar ? sidebar.width + 40 : 40 };
    // Narrow or short viewports must always leave a positive area for fitBounds.
    const verticalScale = Math.min(1, Math.max(0, frame.height - 120) / (padding.top + padding.bottom));
    const horizontalScale = Math.min(1, Math.max(0, frame.width - 160) / (padding.left + padding.right));
    map.fitBounds(bounds, {
      padding: { top: padding.top * verticalScale, bottom: padding.bottom * verticalScale, left: padding.left * horizontalScale, right: padding.right * horizontalScale },
      maxZoom: routeBounds ? 15.5 : 16,
      bearing: 0,
      pitch: 0,
      duration: reducedMotion ? 0 : 550,
    });
  }, [activeFocus, data, labelLayer, mapRef, station, visibleRouteIds]);

  // Search and map clicks share one selected station; a route popup must not linger.
  useEffect(() => { if (station) setPopup(null); }, [station]);

  useEffect(() => {
    if (!labelLayer) return;
    const map = mapRef.current!;
    const canvas = map.getCanvas();
    canvas.setAttribute('aria-label', '互動地圖；方向鍵移動，Enter 開啟畫面中央最近的可見站點，Escape 關閉資訊');
    const openStation = (event: KeyboardEvent) => {
      if (event.key !== 'Enter') return;
      const layers = [...(activeFocus ? ['focus-bus-stops', 'focus-mrt-stations', 'focus-youbike'] : []), ...STATION_LAYERS.filter((id) => state.layers.has(LAYER_GROUP_OF[id]))].filter((id) => map.getLayer(id));
      if (!layers.length) return;
      const centre = { x: canvas.clientWidth / 2, y: canvas.clientHeight / 2 };
      const stations = map.queryRenderedFeatures(undefined, { layers });
      const distance = (f: MapGeoJSONFeature) => {
        const point = map.project((f.geometry as Point).coordinates as [number, number]);
        return Math.hypot(point.x - centre.x, point.y - centre.y);
      };
      stations.sort((a, b) => distance(a) - distance(b));
      const feature = stations[0];
      const next = feature && stationFromFeature({ ...feature, geometry: feature.geometry, layer: { ...feature.layer, id: FOCUS_ORIGINAL_LAYERS[feature.layer.id] ?? feature.layer.id } } as MapGeoJSONFeature, data);
      if (next) { event.preventDefault(); setHover(null); setPopup(null);
        if (editor?.interaction === 'stops' && next.type === 'bus') editor.onStop(next.id);
        else onStation(next, canvas);
      }
    };
    canvas.addEventListener('keydown', openStation);
    return () => canvas.removeEventListener('keydown', openStation);
  }, [activeFocus !== null, data, editor, labelLayer, mapRef, onStation, state.layers]);

  const handleLoad = useCallback(() => {
    const map = mapRef.current!.getMap();
    // The container can be measured before the stylesheet applies; re-measure once ready.
    map.resize();
    // On phones the expanded attribution wraps over the bottom cards, so it starts as its ⓘ button.
    // MapLibre only expands it once the attribution text arrives, hence waiting for the first idle.
    if (map.getContainer().clientWidth <= 640) {
      map.once('idle', () => map.getContainer().querySelector('.maplibregl-compact-show')?.classList.remove('maplibregl-compact-show'));
    }
    addHatchPattern(map);
    const labels = map.getStyle().layers.filter((l) => l.type === 'symbol');
    for (const layer of labels) {
      if (JSON.stringify(map.getLayoutProperty(layer.id, 'text-field') ?? '').includes('name')) {
        map.setLayoutProperty(layer.id, 'text-field', LABEL_NAME);
      }
    }
    baseLabels.current = labels.map((l) => l.id);
    setLabelLayer(labels[0]?.id ?? null);
    onReady();
  }, [mapRef, onReady]);

  // Base-map place and road names follow the label preference.
  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!map || labelLayer === null) return;
    for (const id of baseLabels.current) map.setLayoutProperty(id, 'visibility', state.labels ? 'visible' : 'none');
  }, [mapRef, labelLayer, state.labels]);

  // The frequency grid belongs to frequency mode; coverage mode draws its own raster instead.
  const shown = (group: LayerGroup) => state.layers.has(group) && (group !== 'grid' || (!editor && !activeFocus && state.mode === 'frequency'));
  const visible = (id: string) => (shown(LAYER_GROUP_OF[id]) ? 'visible' : 'none');
  const withVisibility = (layer: L.StyleLayer, beforeId?: string): LayerProps => ({
    ...layer,
    beforeId,
    layout: { ...(layer.layout ?? {}), visibility: visible(layer.id!) },
    ...(editor && layer.id === 'bus-routes' ? { paint: { ...layer.paint, 'line-color': '#8aa2af', 'line-opacity': .025, 'line-width': .75 } } : {}),
    ...(editor && (layer.id === 'mrt-lines' || layer.id === 'mrt-lines-casing') ? { paint: { ...layer.paint, 'line-opacity': .2 } } : {}),
    ...(editor && layer.id === 'places' ? { paint: { ...layer.paint, 'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 2, 15, 4], 'circle-opacity': .65, 'circle-stroke-width': 1 } } : {}),
    ...(activeFocus && layer.id === 'bus-routes' ? { paint: { ...layer.paint, 'line-color': '#b7c1c6', 'line-opacity': .2, 'line-width': .8 } } : {}),
    ...(activeFocus && layer.id === 'mrt-lines' ? { paint: { ...layer.paint, 'line-color': '#b7c1c6', 'line-opacity': .3, 'line-width': 1.4 } } : {}),
    ...(activeFocus && layer.id === 'mrt-lines-casing' ? { paint: { ...layer.paint, 'line-opacity': .3 } } : {}),
    ...(activeFocus && ['bus-stops', 'mrt-stations', 'youbike', 'places'].includes(layer.id!) ? { paint: { ...layer.paint, 'circle-opacity': .2, 'circle-stroke-opacity': .2 } } : {}),
  } as LayerProps);

  const placeMembers = useMemo(
    () => PLACE_CATEGORIES.filter((c) => state.places.has(c.key)).flatMap((c) => c.members),
    [state.places],
  );

  const featureAt = (e: MapLayerMouseEvent): MapGeoJSONFeature | undefined => {
    const map = mapRef.current!.getMap();
    const layers = [...(activeFocus ? Object.keys(FOCUS_ORIGINAL_LAYERS) : []), ...HOVER_LAYERS.filter((id) => (!activeFocus || id !== 'bus-routes') && shown(LAYER_GROUP_OF[id]))].filter((id) => map.getLayer(id));
    // In need mode the grid cell outranks route lines, but point markers still win.
    if (state.mode === 'need' && map.getLayer('need-fill')) {
      const firstLine = layers.findIndex((id) => id === 'bus-routes' || id === 'mrt-lines');
      layers.splice(firstLine < 0 ? layers.length : firstLine, 0, 'need-fill');
    }
    const { x, y } = e.point;
    const features = map.queryRenderedFeatures([[x - 4, y - 4], [x + 4, y + 4]], { layers }).map((feature) => {
      const original = FOCUS_ORIGINAL_LAYERS[feature.layer.id];
      return original ? { ...feature, geometry: feature.geometry, layer: { ...feature.layer, id: original } } as MapGeoJSONFeature : feature;
    });
    // Station points win; retain the analysis mode's priority for all other features.
    const stationFeature = features.find((f) => STATION_LAYERS.includes(f.layer.id));
    if (stationFeature) return stationFeature;
    const priority = (f: MapGeoJSONFeature) => layers.findIndex((id) => (FOCUS_ORIGINAL_LAYERS[id] ?? id) === f.layer.id);
    return features.reduce<MapGeoJSONFeature | undefined>((best, f) => (!best || priority(f) < priority(best) ? f : best), undefined);
  };

  const handleMouseMove = (e: MapLayerMouseEvent) => {
    const feature = featureAt(e);
    const canvas = mapRef.current!.getCanvas();
    canvas.style.cursor = feature && !/^(grid|need)/.test(feature.layer.id) ? 'pointer' : '';
    const { x, y } = e.point;
    if (feature) setHover({ feature, x, y });
    else if (!activeFocus && state.mode === 'coverage' && coverage) {
      const distances = distancesAt(coverage, e.lngLat.lng, e.lngLat.lat);
      setHover(distances ? { distances, x, y } : null);
    } else setHover(null);
  };

  const handleClick = (e: MapLayerMouseEvent) => {
    const feature = featureAt(e);
    const id = feature?.layer.id;
    if (feature && STATION_LAYERS.includes(id!)) {
      if (editor?.interaction === 'stops' && id === 'bus-stops') {
        editor.onStop(String(feature.properties.id)); setHover(null); return;
      }
      onStation(stationFromFeature(feature, data), mapRef.current!.getCanvas());
      setPopup(null);
      setHover(null);
    } else if (feature && id === 'bus-routes') {
      if (editor) { editor.onRoute(String(feature.properties.route_id)); setHover(null); return; }
      if (activeFocus) { onFocusRoute?.(String(feature.properties.route_id)); setHover(null); return; }
      onStation(null);
      setPopup({ feature, lngLat: [e.lngLat.lng, e.lngLat.lat] });
      setHover(null);
    } else {
      setPopup(null);
      if (station && !activeFocus) onCloseStation();
    }
  };

  const highlightedRoutes = activeFocus ? null
    : popup?.feature.layer.id === 'bus-routes' ? [String(popup.feature.properties.route_id)]
      : station?.type === 'bus' ? station.properties.route_ids ?? [] : null;
  const below = labelLayer ?? undefined;

  return (
    <>
      <Map
        ref={mapRef}
        mapStyle={BASE_STYLE}
        workerUrl={workerUrl}
        initialViewState={{ longitude: initialView.center[0], latitude: initialView.center[1], zoom: initialView.zoom }}
        localIdeographFontFamily="'Noto Sans TC', 'PingFang TC', 'Microsoft JhengHei', sans-serif"
        minZoom={9.5}
        maxZoom={17}
        maxBounds={[121.2, 24.8, 121.9, 25.4]}
        attributionControl={false}
        style={{ position: 'absolute', inset: 0 }}
        onLoad={handleLoad}
        onMouseMove={handleMouseMove}
        onMouseOut={() => setHover(null)}
        onClick={handleClick}
        onZoom={(e) => {
          const zoom = e.viewState.zoom;
          const show = zoom >= 10.5 && zoom <= 14.5;
          if (show !== showDistrictLabels) setShowDistrictLabels(show);
        }}
        onMoveEnd={(e) => onMoveEnd({ center: [e.viewState.longitude, e.viewState.latitude], zoom: e.viewState.zoom })}
      >
        <NavigationControl position="top-right" visualizePitch={false} />
        {/* Same corner in every view; floating cards keep 40 px clear at the bottom for it. */}
        <AttributionControl position="bottom-right" compact customAttribution="公車：臺北市公共運輸處｜行政界：內政部國土測繪中心" />

        {labelLayer !== null && (
          <>
            <Source id="grid" type="geojson" data={gridData}>
              <Layer {...withVisibility(L.gridFillLayer, below)} />
              <Layer {...withVisibility(L.gridHatchLayer, below)} />
            </Source>
            {editor && <ScenarioGrid editor={editor} beforeId={below} />}
            {need && (
              <Source id="need" type="geojson" data={need.data}>
                <Layer {...L.needFillLayer} beforeId={below} layout={{ visibility: !activeFocus && state.mode === 'need' ? 'visible' : 'none' }} />
                <Layer {...L.needPartialLayer} beforeId={below} layout={{ visibility: !activeFocus && state.mode === 'need' ? 'visible' : 'none' }} />
                <Layer {...L.needRobustLayer} beforeId={below} layout={{ visibility: !activeFocus && state.mode === 'need' ? 'visible' : 'none' }} />
              </Source>
            )}
            {coverageImage && (
              <Source id="coverage" type="image" url={coverageImage.url} coordinates={coverageImage.coordinates}>
                <Layer {...L.coverageLayer} beforeId={below} layout={{ visibility: !activeFocus && state.mode === 'coverage' ? 'visible' : 'none' }} />
              </Source>
            )}
            {coverageLines && coverage && (
              <Source id="coverage-grid" type="geojson" data={coverageLines}>
                <Layer {...L.coverageGridLayer(coverage.cell_m)} beforeId={below} layout={{ visibility: !activeFocus && state.mode === 'coverage' && state.gridLines ? 'visible' : 'none' }} />
              </Source>
            )}
            <Source id="roads" type="geojson" data={data.roads}>
              <Layer {...L.roadsLayer} beforeId={below} />
            </Source>
            <Source id="districts" type="geojson" data={data.districts}>
              <Layer {...L.districtsLayer} beforeId={below} />
            </Source>
            <Source id="taipei" type="geojson" data={data.taipei_boundary}>
              <Layer {...L.taipeiBoundaryLayer} beforeId={below} />
            </Source>
            {catchmentShape && (
              <Source id="catchment" type="geojson" data={catchmentShape}>
                <Layer {...{ ...L.catchmentFillLayer, ...(activeFocus ? { paint: { 'fill-color': '#4b8793', 'fill-opacity': .1 } } : {}) } as LayerProps} beforeId={below} />
                <Layer {...{ ...L.catchmentLineLayer, ...(activeFocus ? { paint: { 'line-color': '#4b8793', 'line-opacity': .7, 'line-width': 1.5, 'line-dasharray': [3, 2] } } : {}) } as LayerProps} beforeId={below} />
              </Source>
            )}
            <Source id="bus-routes" type="geojson" data={data.bus_routes}>
              <Layer {...withVisibility(L.busRoutesLayer, below)} />
              <Layer {...L.busRouteHighlightLayer(highlightedRoutes)} beforeId={below} />
              {activeFocus && L.busFocusRouteLayers(activeFocus).map((layer) => <Layer key={layer.id} {...layer} beforeId={below} />)}
            </Source>
            <Source id="mrt-lines" type="geojson" data={data.mrt_lines}>
              <Layer {...withVisibility(L.mrtCasingLayer, below)} />
              <Layer {...withVisibility(L.mrtLinesLayer, below)} />
            </Source>
            <Source id="mrt-stations" type="geojson" data={data.mrt_stations}>
              <Layer {...withVisibility(L.mrtStationsLayer)} />
            </Source>
            <Source id="bus-stops" type="geojson" data={data.bus_stops}>
              <Layer {...withVisibility(L.busStopsLayer)} />
            </Source>
            <Source id="youbike" type="geojson" data={data.youbike_stations}>
              <Layer {...withVisibility(L.youbikeLayer)} />
            </Source>
            <Source id="places" type="geojson" data={data.places}>
              <Layer {...withVisibility(L.placesLayer(placeMembers))} />
            </Source>
            {nearbyAndRouteStops && <>
              <Source id="focus-bus-stops" type="geojson" data={nearbyAndRouteStops.bus}>
                <Layer {...L.focusBusStopsLayer} />
              </Source>
              <Source id="focus-mrt-stations" type="geojson" data={nearbyAndRouteStops.mrt}>
                <Layer {...L.mrtStationsLayer} id="focus-mrt-stations" minzoom={0} />
              </Source>
              <Source id="focus-youbike" type="geojson" data={nearbyAndRouteStops.youbike}>
                <Layer {...L.youbikeLayer} id="focus-youbike" minzoom={0} />
              </Source>
              <Source id="focus-places" type="geojson" data={nearbyAndRouteStops.places}>
                <Layer {...L.placesLayer(placeMembers)} id="focus-places" />
              </Source>
            </>}
            {editor && <RouteLayers editor={editor} beforeId={below} />}
          </>
        )}

        {showDistrictLabels && state.labels && data.district_labels.features.map((f) => (
          <Marker key={f.properties.name + f.properties.city_code} longitude={f.geometry.coordinates[0]} latitude={f.geometry.coordinates[1]}>
            <div className={`district-label${f.properties.city_code === '63000' ? '' : ' is-ntpc'}`}>{f.properties.name}</div>
          </Marker>
        ))}

        {station && <Marker longitude={station.at[0]} latitude={station.at[1]} anchor="center">
          {activeFocus ? <div className="station-focus-marker">
            <div className="station-pin station-bus" aria-hidden="true"><StationIcon type="bus" /></div>
            <span className="station-focus-name">{station.properties.name}</span>
          </div> : <div className={`station-pin station-${station.type}`} aria-hidden="true"><StationIcon type={station.type} /></div>}
        </Marker>}
        {popup && !station && (
          <Popup longitude={popup.lngLat[0]} latitude={popup.lngLat[1]} offset={8} onClose={() => setPopup(null)} closeOnClick={false}>
            <RoutePopup feature={popup.feature} />
          </Popup>
        )}
      </Map>

      {station && <StationInfo station={station} mode={activeFocus ? 'focus' : 'popup'} mapRef={mapRef} data={data} placeKeys={state.places} onClose={onCloseStation} />}

      {hover && (
        <div ref={tooltipRef} className="tooltip" style={{ left: hover.x, top: hover.y }}>
          {hover.feature?.layer.id === 'need-fill' && need
            ? <NeedTooltip i={Number(hover.feature.properties.i)} grid={need.grid} result={need.result} state={state} />
            : hover.feature
            ? <FeatureTooltip feature={hover.feature} grid={grid} state={state} />
            : hover.distances && <CoverageTooltip distances={hover.distances} state={state} />}
        </div>
      )}
    </>
  );
}
