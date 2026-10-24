import type { FeatureCollection, Point, Polygon } from 'geojson';
import type { MapGeoJSONFeature, Map as MaplibreMap, Popup as MaplibrePopup } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
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
import { circle, nearby } from '../catchment';
import { BASE_STYLE, COVERAGE_LAYERS, HOVER_LAYERS, LAYER_GROUPS, PLACE_CATEGORIES } from '../config';
import { distancesAt, type CoverageImage } from '../coverage';
import { loadCatchments } from '../data';
import type { NeedCellProps, NeedResult } from '../need';
import { CoverageTooltip, FeatureTooltip, NeedTooltip, RoutePopup, StopPopup, stopRoutes, type PopupFeature } from './FeatureInfo';
import * as L from './layers';
import type { AppState, Coverage, Grid, LayerGroup, MapData, NeedGrid, StopCatchments, ViewState } from '../types';

const LABEL_NAME = ['coalesce', ['get', 'name:zh-Hant'], ['get', 'name:nonlatin'], ['get', 'name']];
const LAYER_GROUP_OF: Record<string, LayerGroup> = Object.fromEntries(
  Object.entries(LAYER_GROUPS).flatMap(([group, ids]) => ids.map((id) => [id, group as LayerGroup])),
);

interface Props {
  mapRef: RefObject<MapRef | null>;
  initialView: ViewState;
  data: MapData;
  grid: Grid;
  gridData: FeatureCollection<Polygon, { k: number }>;
  coverage: Coverage | null;
  coverageImage: CoverageImage | null;
  need: { grid: NeedGrid; result: NeedResult; data: FeatureCollection<Polygon, NeedCellProps> } | null;
  state: AppState;
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
}

export default function MapView({ mapRef, initialView, data, grid, gridData, coverage, coverageImage, need, state, onReady, onMoveEnd }: Props) {
  // First label layer of the base style: our layers go below it so road names stay on top.
  const [labelLayer, setLabelLayer] = useState<string | null>(null);
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
  const radii = COVERAGE_LAYERS.walk.radii;
  const [catchRadius, setCatchRadius] = useState(radii.includes(state.walkRadius) ? state.walkRadius : COVERAGE_LAYERS.walk.radius);
  const [catchments, setCatchments] = useState<StopCatchments | null>(null);

  const stop = popup?.feature.layer.id === 'bus-stops' ? popup : null;
  // Population per stop is only needed once a stop is opened.
  useEffect(() => {
    if (stop && !catchments) loadCatchments().then(setCatchments, () => {});
  }, [stop, catchments]);
  const stopId = stop?.feature.properties.id as string | undefined;
  const stopNearby = useMemo(
    () => (stop ? nearby(data, stop.lngLat, catchRadius, stopId!, state.places) : null),
    [data, stop, catchRadius, stopId, state.places],
  );
  const catchmentShape = useMemo(() => (stop ? circle(stop.lngLat, catchRadius) : null), [stop, catchRadius]);

  // The stop popup is tall; pan the map once it opens so the whole popup is visible.
  const popupRef = useRef<MaplibrePopup>(null);
  useEffect(() => {
    if (!stop) return;
    const frame = requestAnimationFrame(() => {
      const el = popupRef.current?.getElement();
      const map = mapRef.current;
      if (!el || !map) return;
      const tip = el.getBoundingClientRect();
      const box = map.getContainer().getBoundingClientRect();
      const margin = 12;
      const dx = Math.max(0, tip.right + margin - box.right) - Math.max(0, box.left + margin - tip.left);
      const dy = Math.max(0, tip.bottom + margin - box.bottom) - Math.max(0, box.top + margin - tip.top);
      if (dx || dy) map.panBy([dx, dy]);
    });
    return () => cancelAnimationFrame(frame);
  }, [stop, mapRef]);

  const handleLoad = useCallback(() => {
    const map = mapRef.current!.getMap();
    // The container can be measured before the stylesheet applies; re-measure once ready.
    map.resize();
    addHatchPattern(map);
    const labels = map.getStyle().layers.filter((l) => l.type === 'symbol');
    for (const layer of labels) {
      if (JSON.stringify(map.getLayoutProperty(layer.id, 'text-field') ?? '').includes('name')) {
        map.setLayoutProperty(layer.id, 'text-field', LABEL_NAME);
      }
    }
    setLabelLayer(labels[0]?.id ?? null);
    onReady();
  }, [mapRef, onReady]);

  // The frequency grid belongs to frequency mode; coverage mode draws its own raster instead.
  const shown = (group: LayerGroup) => state.layers.has(group) && (group !== 'grid' || state.mode === 'frequency');
  const visible = (id: string) => (shown(LAYER_GROUP_OF[id]) ? 'visible' : 'none');
  const withVisibility = (layer: L.StyleLayer, beforeId?: string): LayerProps => ({
    ...layer,
    beforeId,
    layout: { ...(layer.layout ?? {}), visibility: visible(layer.id!) },
  } as LayerProps);

  const placeMembers = useMemo(
    () => PLACE_CATEGORIES.filter((c) => state.places.has(c.key)).flatMap((c) => c.members),
    [state.places],
  );

  const featureAt = (e: MapLayerMouseEvent): MapGeoJSONFeature | undefined => {
    const map = mapRef.current!.getMap();
    const layers = HOVER_LAYERS.filter((id) => map.getLayer(id) && shown(LAYER_GROUP_OF[id]));
    // In need mode the grid cell outranks route lines, but point markers still win.
    if (state.mode === 'need' && map.getLayer('need-fill')) {
      const firstLine = layers.findIndex((id) => id === 'bus-routes' || id === 'mrt-lines');
      layers.splice(firstLine < 0 ? layers.length : firstLine, 0, 'need-fill');
    }
    const { x, y } = e.point;
    const features = map.queryRenderedFeatures([[x - 4, y - 4], [x + 4, y + 4]], { layers });
    const priority = (f: MapGeoJSONFeature) => layers.indexOf(f.layer.id);
    return features.reduce<MapGeoJSONFeature | undefined>((best, f) => (!best || priority(f) < priority(best) ? f : best), undefined);
  };

  const handleMouseMove = (e: MapLayerMouseEvent) => {
    const feature = featureAt(e);
    const canvas = mapRef.current!.getCanvas();
    canvas.style.cursor = feature && !/^(grid|need)/.test(feature.layer.id) ? 'pointer' : '';
    const { x, y } = e.point;
    if (feature) setHover({ feature, x, y });
    else if (state.mode === 'coverage' && coverage) {
      const distances = distancesAt(coverage, e.lngLat.lng, e.lngLat.lat);
      setHover(distances ? { distances, x, y } : null);
    } else setHover(null);
  };

  const handleClick = (e: MapLayerMouseEvent) => {
    const feature = featureAt(e);
    const id = feature?.layer.id;
    if (feature && id === 'bus-stops') {
      // Centre the catchment on the stop itself, not on the click.
      const [lng, lat] = (feature.geometry as Point).coordinates;
      setPopup({ feature, lngLat: [lng, lat] });
      setHover(null);
    } else if (feature && id === 'bus-routes') {
      setPopup({ feature, lngLat: [e.lngLat.lng, e.lngLat.lat] });
      setHover(null);
    } else {
      setPopup(null);
    }
  };

  const highlightedRoutes = popup?.feature.layer.id === 'bus-routes'
    ? { routeId: popup.feature.properties.route_id as string }
    : stop ? { names: stopRoutes(stop.feature) } : null;
  const below = labelLayer ?? undefined;

  return (
    <>
      <Map
        ref={mapRef}
        mapStyle={BASE_STYLE}
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
        <AttributionControl position="top-right" compact customAttribution="公車：臺北市公共運輸處｜行政界：內政部國土測繪中心" />

        {labelLayer !== null && (
          <>
            <Source id="grid" type="geojson" data={gridData}>
              <Layer {...withVisibility(L.gridFillLayer, below)} />
              <Layer {...withVisibility(L.gridHatchLayer, below)} />
            </Source>
            {need && (
              <Source id="need" type="geojson" data={need.data}>
                <Layer {...L.needFillLayer} beforeId={below} layout={{ visibility: state.mode === 'need' ? 'visible' : 'none' }} />
                <Layer {...L.needPartialLayer} beforeId={below} layout={{ visibility: state.mode === 'need' ? 'visible' : 'none' }} />
                <Layer {...L.needRobustLayer} beforeId={below} layout={{ visibility: state.mode === 'need' ? 'visible' : 'none' }} />
              </Source>
            )}
            {coverageImage && (
              <Source id="coverage" type="image" url={coverageImage.url} coordinates={coverageImage.coordinates}>
                <Layer {...L.coverageLayer} beforeId={below} layout={{ visibility: state.mode === 'coverage' ? 'visible' : 'none' }} />
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
          </>
        )}

        {showDistrictLabels && data.district_labels.features.map((f) => (
          <Marker key={f.properties.name + f.properties.city_code} longitude={f.geometry.coordinates[0]} latitude={f.geometry.coordinates[1]}>
            <div className={`district-label${f.properties.city_code === '63000' ? '' : ' is-ntpc'}`}>{f.properties.name}</div>
          </Marker>
        ))}

        {popup && (
          <Popup ref={popupRef} longitude={popup.lngLat[0]} latitude={popup.lngLat[1]} offset={8} onClose={() => setPopup(null)} closeOnClick={false}>
            {stop && stopNearby ? (
              <StopPopup
                feature={stop.feature}
                radii={radii}
                radius={catchRadius}
                onRadius={setCatchRadius}
                catchment={catchments ? (catchments.stops[stopId!] ?? null) : undefined}
                nearby={stopNearby}
              />
            ) : <RoutePopup feature={popup.feature} />}
          </Popup>
        )}
      </Map>

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
