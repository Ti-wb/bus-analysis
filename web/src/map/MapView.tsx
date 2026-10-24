import type { FeatureCollection, MultiLineString, Point, Polygon } from 'geojson';
import type { ExpressionSpecification, MapGeoJSONFeature, Map as MaplibreMap, StyleSpecification } from 'maplibre-gl';
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
  type MapEvent,
  type MapLayerMouseEvent,
  type MapRef,
} from 'react-map-gl/maplibre';
import { groupFor, type VillageAges, type VillageAgeMap } from '../age';
import { VillageDialog, VillageFacts } from '../components/VillageAge';
import { circle } from '../catchment';
import StationInfo, { StationIcon } from '../components/StationInfo';
import { BASE_STYLE, HOVER_LAYERS, LAYER_GROUPS, PLACE_CATEGORIES } from '../config';
import { distancesAt, type CoverageImage } from '../coverage';
import type { NeedCellProps, NeedResult } from '../need';
import { travelCollection } from '../regional';
import { STATION_LAYERS, STATION_RADIUS, stationFromFeature } from '../stations';
import { CoverageTooltip, FeatureTooltip, NeedTooltip, RoutePopup, type PopupFeature } from './FeatureInfo';
import * as L from './layers';
import { ROUTE_STOP_PICKER, RouteLayers, RouteStopPicker, ScenarioGrid, type EditorMapProps } from '../routeEditor/RouteLayers';
import type { AppState, Coverage, Grid, LayerGroup, MapData, NeedGrid, StationSelection, ViewState } from '../types';

const LABEL_NAME: ExpressionSpecification = ['coalesce', ['get', 'name:zh-Hant'], ['get', 'name:nonlatin'], ['get', 'name']];
const LOCAL_STYLE: StyleSpecification = {
  version: 8, sources: {}, layers: [{ id: 'local-background', type: 'background', paint: { 'background-color': '#eef3f7' } }],
};
const LAYER_GROUP_OF: Record<string, LayerGroup> = Object.fromEntries(
  Object.entries(LAYER_GROUPS).flatMap(([group, ids]) => ids.map((id) => [id, group as LayerGroup])),
);
const POPUP_LAYERS = ['bus-routes', 'regional-bus-routes', 'traffic-sections', 'bus-travel-times', 'villages-fill'];

interface Props {
  villageAges?: VillageAges | null;
  villageData?: VillageAgeMap | null;
  selectedVillage?: string | null;
  onVillage?: (id: string | null, fly?: boolean) => void;
  onCloseVillage?: () => void;
  editor?: EditorMapProps;
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
  if (!map.hasImage('hatch')) map.addImage('hatch', ctx.getImageData(0, 0, size, size));

  // Transparent hatch drawn over coloured cells (transport-need cells with missing schedules).
  ctx.clearRect(0, 0, size, size);
  ctx.strokeStyle = 'rgba(40,40,40,0.55)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, size);
  ctx.lineTo(size, 0);
  ctx.stroke();
  if (!map.hasImage('hatch-line')) map.addImage('hatch-line', ctx.getImageData(0, 0, size, size));
  ctx.clearRect(0, 0, size, size);
  ctx.strokeStyle = '#be4966';
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(0, size); ctx.lineTo(size, 0); ctx.stroke();
  if (!map.hasImage('scenario-hatch')) map.addImage('scenario-hatch', ctx.getImageData(0, 0, size, size));
}

export default function MapView({ villageAges = null, villageData = null, selectedVillage = null, onVillage, onCloseVillage, editor, mapRef, initialView, data, grid, gridData, coverage, coverageImage, coverageLines, need, state, station, onStation, onCloseStation, onReady, onMoveEnd }: Props) {
  // First label layer of the base style: our layers go below it so road names stay on top.
  const [labelLayer, setLabelLayer] = useState<string | null>(null);
  const [styleLoaded, setStyleLoaded] = useState(false);
  const [localBase, setLocalBase] = useState(false);
  const [fallbackView, setFallbackView] = useState<ViewState | null>(null);
  const notifiedReady = useRef(false);
  const [mapNotice, setMapNotice] = useState<string | null>(null);
  const baseLabels = useRef<string[]>([]);
  const [hover, setHover] = useState<Hover | null>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const pickingStops = editor?.interaction === 'stops';
  useEffect(() => { setHover(null); }, [editor?.interaction]);

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
  const travelData = useMemo(() => travelCollection(data.bus_travel_times, state), [data.bus_travel_times, state.day, state.hour]);
  const useLocalBase = useCallback(() => {
    const map = mapRef.current;
    if (map) {
      const center = map.getCenter();
      setFallbackView({ center: [center.lng, center.lat], zoom: map.getZoom() });
    }
    setStyleLoaded(false);
    setLabelLayer(null);
    setLocalBase(true);
    setMapNotice('已使用本地簡圖；道路、行政界與資料圖層可繼續操作，沒有線上底圖路名。');
  }, [mapRef]);

  useEffect(() => {
    if (styleLoaded || localBase) return;
    const timer = setTimeout(useLocalBase, 12_000);
    return () => clearTimeout(timer);
  }, [styleLoaded, localBase, useLocalBase]);

  const villageIndex = useMemo(() => new globalThis.Map(villageAges?.features.map((feature) => [feature.properties.boundary_id, feature.properties]) ?? []), [villageAges]);
  const currentAgeGroup = villageAges && groupFor(villageAges.metadata, state.ageGroup, state.ageSet);
  const selectedVillageProps = selectedVillage ? villageIndex.get(selectedVillage) : undefined;
  const closeVillage = useCallback(() => {
    if (onCloseVillage) onCloseVillage();
    else mapRef.current?.getCanvas().focus({ preventScroll: true });
  }, [mapRef, onCloseVillage]);

  // Search and map clicks share one selected station; a route popup must not linger.
  useEffect(() => { if (station || selectedVillage) { setPopup(null); setHover(null); } }, [station, selectedVillage]);
  // MapLibre event features are snapshots: discard values when their time or filter changes.
  useEffect(() => { setPopup(null); setHover(null); }, [state.day, state.hour, state.villageMetric, state.leapfrogOnly, state.layers, state.mode, state.ageGroup, state.ageSet]);

  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current!;
    const canvas = map.getCanvas();
    canvas.setAttribute('aria-label', pickingStops
      ? '選擇公車站；方向鍵移動，Enter 加入畫面中央最近的可選公車站'
      : state.mode === 'village_age' && !editor
      ? '互動地圖；方向鍵移動，Enter 查詢畫面中央的村里年齡，Escape 關閉資訊'
      : '互動地圖；方向鍵移動，Enter 開啟畫面中央最近的可見站點，Escape 關閉資訊');
    const openStation = (event: KeyboardEvent) => {
      if (event.key !== 'Enter') return;
      if (!editor && state.mode === 'village_age' && map.getLayer('village-age-fill')) {
        const centre: [number, number] = [canvas.clientWidth / 2, canvas.clientHeight / 2];
        const village = map.queryRenderedFeatures(centre, { layers: ['village-age-fill'] })[0];
        if (village) { event.preventDefault(); onStation(null); onVillage?.(String(village.properties.boundary_id), false); setHover(null); }
        return;
      }
      const layers = pickingStops ? (map.getLayer(ROUTE_STOP_PICKER) ? [ROUTE_STOP_PICKER] : [])
        : STATION_LAYERS.filter((id) => map.getLayer(id) && state.layers.has(LAYER_GROUP_OF[id]));
      if (!layers.length) return;
      const centre = { x: canvas.clientWidth / 2, y: canvas.clientHeight / 2 };
      const stations = map.queryRenderedFeatures(undefined, { layers });
      const distance = (f: MapGeoJSONFeature) => {
        const point = map.project((f.geometry as Point).coordinates as [number, number]);
        return Math.hypot(point.x - centre.x, point.y - centre.y);
      };
      stations.sort((a, b) => distance(a) - distance(b));
      if (pickingStops && editor && stations[0]) {
        event.preventDefault(); setHover(null); setPopup(null);
        editor.onStop(String(stations[0].properties.id)); return;
      }
      const next = stations[0] && stationFromFeature(stations[0], data);
      if (next) { event.preventDefault(); setHover(null); setPopup(null);
        onStation(next, canvas);
      }
    };
    canvas.addEventListener('keydown', openStation);
    return () => canvas.removeEventListener('keydown', openStation);
  }, [data, editor, styleLoaded, mapRef, onStation, onVillage, pickingStops, state.layers, state.mode]);

  const handleLoad = useCallback((event: MapEvent) => {
    const map = event.target;
    // The container can be measured before the stylesheet applies; re-measure once ready.
    map.resize();
    // On phones the expanded attribution wraps over the bottom cards, so it starts as its ⓘ button.
    // MapLibre only expands it once the attribution text arrives, hence waiting for the first idle.
    if (map.getContainer().clientWidth <= 640) {
      map.once('idle', () => map.getContainer().querySelector('.maplibregl-compact-show')?.classList.remove('maplibregl-compact-show'));
    }
    addHatchPattern(map);
    const labels = map.getStyle().layers?.filter((l) => l.type === 'symbol') ?? [];
    for (const layer of labels) {
      if (JSON.stringify(map.getLayoutProperty(layer.id, 'text-field') ?? '').includes('name')) {
        map.setLayoutProperty(layer.id, 'text-field', LABEL_NAME);
      }
    }
    baseLabels.current = labels.map((l) => l.id);
    setLabelLayer(labels[0]?.id ?? null);
    setStyleLoaded(true);
  }, []);

  useEffect(() => {
    // Replacing the basemap must not repeat the route workspace's initial fit.
    // Notify after the replacement's ref has been attached.
    if (styleLoaded && !notifiedReady.current) {
      notifiedReady.current = true;
      onReady();
    }
  }, [styleLoaded, onReady]);

  // Base-map place and road names follow the label preference.
  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!map || !styleLoaded) return;
    for (const id of baseLabels.current) map.setLayoutProperty(id, 'visibility', state.labels ? 'visible' : 'none');
  }, [mapRef, styleLoaded, state.labels]);

  // The frequency grid belongs to frequency mode; coverage mode draws its own raster instead.
  const ageView = !editor && state.mode === 'village_age';
  const villageView = !ageView && state.layers.has('villages');
  const shown = (group: LayerGroup) => state.layers.has(group) && !(ageView && group === 'villages') && (group !== 'grid' || (!editor && state.mode === 'frequency' && !villageView));
  const visible = (id: string) => (shown(LAYER_GROUP_OF[id]) ? 'visible' : 'none');
  const withVisibility = (layer: L.StyleLayer, beforeId?: string): LayerProps => ({
    ...layer,
    beforeId,
    layout: { ...(layer.layout ?? {}), visibility: pickingStops && layer.id === 'bus-stops' ? 'none' : visible(layer.id!) },
    ...(editor && layer.id === 'bus-routes' ? { paint: { ...layer.paint, 'line-color': '#8aa2af', 'line-opacity': .025, 'line-width': .75 } } : {}),
    ...(editor && (layer.id === 'mrt-lines' || layer.id === 'mrt-lines-casing') ? { paint: { ...layer.paint, 'line-opacity': .2 } } : {}),
    ...(editor && layer.id === 'places' ? { paint: { ...layer.paint, 'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 2, 15, 4], 'circle-opacity': .65, 'circle-stroke-width': 1 } } : {}),
  } as LayerProps);

  const placeMembers = useMemo(
    () => PLACE_CATEGORIES.filter((c) => state.places.has(c.key)).flatMap((c) => c.members),
    [state.places],
  );

  const featureAt = (e: MapLayerMouseEvent): MapGeoJSONFeature | undefined => {
    const map = mapRef.current!.getMap();
    const { x, y } = e.point;
    if (pickingStops) {
      if (!map.getLayer(ROUTE_STOP_PICKER)) return;
      // Nearby MRT / YouBike points must not intercept stop selection. Pick the
      // closest bus stop when several enlarged targets overlap.
      const stops = map.queryRenderedFeatures([[x - 8, y - 8], [x + 8, y + 8]], { layers: [ROUTE_STOP_PICKER] });
      const distance = (f: MapGeoJSONFeature) => {
        const point = map.project((f.geometry as Point).coordinates as [number, number]);
        return Math.hypot(point.x - x, point.y - y);
      };
      return stops.reduce<MapGeoJSONFeature | undefined>((best, f) => !best || distance(f) < distance(best) ? f : best, undefined);
    }
    const layers = HOVER_LAYERS.filter((id) => map.getLayer(id) && shown(LAYER_GROUP_OF[id]));
    // In need mode the grid cell outranks route lines, but point markers still win.
    if (state.mode === 'need' && !villageView && map.getLayer('need-fill')) {
      const firstLine = layers.findIndex((id) => id === 'bus-routes' || id === 'mrt-lines' || id === 'regional-bus-routes');
      layers.splice(firstLine < 0 ? layers.length : firstLine, 0, 'need-fill');
    }
    if (ageView && map.getLayer('village-age-fill')) {
      // Analysis polygons outrank supplemental lines; station points remain selectable.
      const firstNonPoint = layers.findIndex((id) => id !== 'places' && !STATION_LAYERS.includes(id));
      layers.splice(firstNonPoint < 0 ? layers.length : firstNonPoint, 0, 'village-age-fill');
    }
    const features = map.queryRenderedFeatures([[x - 4, y - 4], [x + 4, y + 4]], { layers });
    // Station points win; retain the analysis mode's priority for all other features.
    const stationFeature = features.find((f) => STATION_LAYERS.includes(f.layer.id));
    if (stationFeature) return stationFeature;
    const priority = (f: MapGeoJSONFeature) => layers.indexOf(f.layer.id);
    return features.reduce<MapGeoJSONFeature | undefined>((best, f) => (!best || priority(f) < priority(best) ? f : best), undefined);
  };

  const handleMouseMove = (e: MapLayerMouseEvent) => {
    const feature = featureAt(e);
    const canvas = mapRef.current!.getCanvas();
    const clickable = feature && (STATION_LAYERS.includes(feature.layer.id)
      || POPUP_LAYERS.includes(feature.layer.id) || feature.layer.id === 'village-age-fill' || feature.layer.id === ROUTE_STOP_PICKER);
    canvas.style.cursor = clickable ? 'pointer' : '';
    const { x, y } = e.point;
    if (feature) setHover({ feature, x, y });
    else if (state.mode === 'coverage' && !villageView && coverage) {
      const distances = distancesAt(coverage, e.lngLat.lng, e.lngLat.lat);
      setHover(distances ? { distances, x, y } : null);
    } else setHover(null);
  };

  const handleClick = (e: MapLayerMouseEvent) => {
    const feature = featureAt(e);
    const id = feature?.layer.id;
    if (feature && id === ROUTE_STOP_PICKER && pickingStops && editor) {
      editor.onStop(String(feature.properties.id)); setHover(null); setPopup(null); return;
    }
    if (feature && STATION_LAYERS.includes(id!)) {
      onStation(stationFromFeature(feature, data), mapRef.current!.getCanvas());
      setPopup(null);
      setHover(null);
    } else if (feature && id === 'village-age-fill') {
      onStation(null);
      onVillage?.(String(feature.properties.boundary_id), false);
      setPopup(null);
      setHover(null);
    } else if (feature && POPUP_LAYERS.includes(id!)) {
      if (editor && id === 'bus-routes') { editor.onRoute(String(feature.properties.route_id)); setHover(null); return; }
      onStation(null);
      onVillage?.(null, false);
      setPopup({ feature, lngLat: [e.lngLat.lng, e.lngLat.lat] });
      setHover(null);
    } else {
      setPopup(null);
      onVillage?.(null, false);
      if (station) onCloseStation();
    }
  };

  const highlightedRoutes = popup?.feature.layer.id === 'bus-routes'
    ? { routeId: popup.feature.properties.route_id as string }
    : station?.type === 'bus' && station.properties.source_scope !== 'NewTaipei' ? { names: station.properties.routes } : null;
  const below = labelLayer ?? undefined;
  const mapView = fallbackView ?? initialView;

  return (
    <>
      <Map
        key={localBase ? 'local-map' : 'online-map'}
        ref={mapRef}
        mapStyle={localBase ? LOCAL_STYLE : BASE_STYLE}
        workerUrl={workerUrl}
        initialViewState={{ longitude: mapView.center[0], latitude: mapView.center[1], zoom: mapView.zoom }}
        localIdeographFontFamily="'Noto Sans TC', 'PingFang TC', 'Microsoft JhengHei', sans-serif"
        minZoom={8}
        maxZoom={17}
        maxBounds={[121.05, 24.65, 122.15, 25.45]}
        attributionControl={false}
        style={{ position: 'absolute', inset: 0 }}
        onLoad={handleLoad}
        onError={() => setMapNotice(localBase ? '部分資料圖層無法繪製，請重新載入頁面或檢查資料快照。' : '網路底圖部分內容載入失敗；可切換本地簡圖繼續查看資料。')}
        onMouseMove={handleMouseMove}
        onMouseOut={() => { setHover(null); const canvas = mapRef.current?.getCanvas(); if (canvas) canvas.style.cursor = ''; }}
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
        <AttributionControl position="bottom-right" compact customAttribution="公車：臺北市公共運輸處／新北市政府交通局｜人口與行政界：內政部" />

        {styleLoaded && (
          <>
            {data.villages && <Source id="villages" type="geojson" data={data.villages}>
              <Layer {...withVisibility(L.villagesFillLayer(state.villageMetric), below)} />
              <Layer {...withVisibility(L.villagesLineLayer, below)} />
            </Source>}
            <Source id="grid" type="geojson" data={gridData}>
              <Layer {...withVisibility(L.gridFillLayer, below)} />
              <Layer {...withVisibility(L.gridHatchLayer, below)} />
            </Source>
            {editor && <ScenarioGrid editor={editor} beforeId={below} />}
            {!editor && villageData && (
              <Source id="village-age" type="geojson" data={villageData}>
                <Layer {...L.villageAgeFillLayer} beforeId={below} layout={{ visibility: state.mode === 'village_age' ? 'visible' : 'none' }} />
                <Layer {...L.villageAgeOutlineLayer} beforeId={below} filter={['==', ['get', 'boundary_id'], selectedVillage ?? '']} layout={{ visibility: state.mode === 'village_age' ? 'visible' : 'none' }} />
              </Source>
            )}
            {need && (
              <Source id="need" type="geojson" data={need.data}>
                <Layer {...L.needFillLayer} beforeId={below} layout={{ visibility: state.mode === 'need' && !villageView ? 'visible' : 'none' }} />
                <Layer {...L.needPartialLayer} beforeId={below} layout={{ visibility: state.mode === 'need' && !villageView ? 'visible' : 'none' }} />
                <Layer {...L.needRobustLayer} beforeId={below} layout={{ visibility: state.mode === 'need' && !villageView ? 'visible' : 'none' }} />
              </Source>
            )}
            {coverageImage && (
              <Source id="coverage" type="image" url={coverageImage.url} coordinates={coverageImage.coordinates}>
                <Layer {...L.coverageLayer} beforeId={below} layout={{ visibility: state.mode === 'coverage' && !villageView ? 'visible' : 'none' }} />
              </Source>
            )}
            {coverageLines && coverage && (
              <Source id="coverage-grid" type="geojson" data={coverageLines}>
                <Layer {...L.coverageGridLayer(coverage.cell_m)} beforeId={below} layout={{ visibility: state.mode === 'coverage' && !villageView && state.gridLines ? 'visible' : 'none' }} />
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
                <Layer {...L.catchmentFillLayer} beforeId={below} />
                <Layer {...L.catchmentLineLayer} beforeId={below} />
              </Source>
            )}
            <Source id="bus-routes" type="geojson" data={data.bus_routes}>
              <Layer {...withVisibility(L.busRoutesLayer, below)} />
              <Layer {...L.busRouteHighlightLayer(highlightedRoutes)} beforeId={below} />
            </Source>
            {data.regional_bus_routes && <Source id="regional-bus-routes" type="geojson" data={data.regional_bus_routes}>
              <Layer {...withVisibility(L.regionalRoutesLayer(state.leapfrogOnly), below)} />
            </Source>}
            {data.traffic_sections && <Source id="traffic-sections" type="geojson" data={data.traffic_sections}>
              <Layer {...withVisibility(L.trafficSectionsLayer, below)} />
            </Source>}
            {travelData && <Source id="bus-travel-times" type="geojson" data={travelData}>
              <Layer {...withVisibility(L.busTravelTimesLayer, below)} />
            </Source>}
            <Source id="mrt-lines" type="geojson" data={data.mrt_lines}>
              <Layer {...withVisibility(L.mrtCasingLayer, below)} />
              <Layer {...withVisibility(L.mrtLinesLayer, below)} />
            </Source>
            <Source id="places" type="geojson" data={data.places}>
              <Layer {...withVisibility(L.placesLayer(placeMembers))} />
            </Source>
            <Source id="youbike" type="geojson" data={data.youbike_stations}>
              <Layer {...withVisibility(L.youbikeLayer)} />
            </Source>
            {data.regional_bus_stops && <Source id="regional-bus-stops" type="geojson" data={data.regional_bus_stops}>
              <Layer {...withVisibility(L.regionalStopsLayer(state.leapfrogOnly))} />
            </Source>}
            <Source id="bus-stops" type="geojson" data={data.bus_stops}>
              <Layer {...withVisibility(L.busStopsLayer)} />
            </Source>
            <Source id="mrt-stations" type="geojson" data={data.mrt_stations}>
              <Layer {...withVisibility(L.mrtStationsLayer)} />
            </Source>
            {pickingStops && editor && <RouteStopPicker editor={editor}
              hoveredId={hover?.feature?.layer.id === ROUTE_STOP_PICKER ? String(hover.feature.properties.id) : null} />}
            {editor && <RouteLayers editor={editor} beforeId={below} />}
          </>
        )}

        {showDistrictLabels && state.labels && data.district_labels.features.map((f) => (
          <Marker key={f.properties.name + f.properties.city_code} longitude={f.geometry.coordinates[0]} latitude={f.geometry.coordinates[1]}>
            <div className={`district-label${f.properties.city_code === '63000' ? '' : ' is-ntpc'}`}>{f.properties.name}</div>
          </Marker>
        ))}

        {station && <Marker longitude={station.at[0]} latitude={station.at[1]} anchor="center">
          <div className={`station-pin station-${station.type}`} aria-hidden="true"><StationIcon type={station.type} /></div>
        </Marker>}
        {popup && !station && (
          <Popup longitude={popup.lngLat[0]} latitude={popup.lngLat[1]} offset={8} onClose={() => setPopup(null)} closeOnClick={false}>
            <RoutePopup feature={popup.feature} state={state} />
          </Popup>
        )}
      </Map>

      {!editor && state.mode === 'village_age' && selectedVillageProps && villageAges && currentAgeGroup && <VillageDialog properties={selectedVillageProps} metadata={villageAges.metadata} group={currentAgeGroup} onClose={closeVillage} />}

      {mapNotice && <div className="map-notice" role="status">
        <span>{mapNotice}</span>
        {!localBase && <button onClick={useLocalBase}>使用本地簡圖</button>}
        <button className="notice-close" aria-label="關閉底圖提示" onClick={() => setMapNotice(null)}>×</button>
      </div>}

      {station && <StationInfo station={station} mapRef={mapRef} data={data} placeKeys={state.places} onClose={onCloseStation} />}

      {hover && (
        <div ref={tooltipRef} className="tooltip" style={{ left: hover.x, top: hover.y }}>
          {hover.feature?.layer.id === ROUTE_STOP_PICKER && pickingStops && editor
            ? <><b>{hover.feature.properties.name}</b><br />公車站位 #{hover.feature.properties.id}
              <strong className="route-stop-pick-action">＋ 點擊加入第 {editor.insertIndex + 1} 站</strong></>
            : hover.feature?.layer.id === 'village-age-fill' && villageAges && currentAgeGroup && villageIndex.get(String(hover.feature.properties.boundary_id))
            ? <VillageFacts properties={villageIndex.get(String(hover.feature.properties.boundary_id))!} metadata={villageAges.metadata} group={currentAgeGroup} compact />
            : hover.feature?.layer.id === 'need-fill' && need
            ? <NeedTooltip i={Number(hover.feature.properties.i)} grid={need.grid} result={need.result} state={state} />
            : hover.feature
            ? <FeatureTooltip feature={hover.feature} grid={grid} state={state} />
            : hover.distances && <CoverageTooltip distances={hover.distances} state={state} />}
        </div>
      )}
    </>
  );
}
