import type { FeatureCollection, Polygon } from 'geojson';
import type { MapGeoJSONFeature, Map as MaplibreMap } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useCallback, useMemo, useState, type RefObject } from 'react';
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
import { BASE_STYLE, HOVER_LAYERS, LAYER_GROUPS, PLACE_CATEGORIES } from '../config';
import { FeaturePopup, FeatureTooltip, type PopupFeature } from './FeatureInfo';
import * as L from './layers';
import type { AppState, Grid, LayerGroup, MapData, ViewState } from '../types';

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
  state: AppState;
  onReady: () => void;
  onMoveEnd: (view: ViewState) => void;
}

interface Hover {
  feature: MapGeoJSONFeature;
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
}

export default function MapView({ mapRef, initialView, data, grid, gridData, state, onReady, onMoveEnd }: Props) {
  // First label layer of the base style: our layers go below it so road names stay on top.
  const [labelLayer, setLabelLayer] = useState<string | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const [popup, setPopup] = useState<PopupFeature | null>(null);
  const [showDistrictLabels, setShowDistrictLabels] = useState(true);

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

  const visible = (id: string) => (state.layers.has(LAYER_GROUP_OF[id]) ? 'visible' : 'none');
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
    const layers = HOVER_LAYERS.filter((id) => map.getLayer(id) && state.layers.has(LAYER_GROUP_OF[id]));
    const { x, y } = e.point;
    return map.queryRenderedFeatures([[x - 4, y - 4], [x + 4, y + 4]], { layers })[0];
  };

  const handleMouseMove = (e: MapLayerMouseEvent) => {
    const feature = featureAt(e);
    const canvas = mapRef.current!.getCanvas();
    canvas.style.cursor = feature && !feature.layer.id.startsWith('grid') ? 'pointer' : '';
    setHover(feature ? { feature, x: e.point.x, y: e.point.y } : null);
  };

  const handleClick = (e: MapLayerMouseEvent) => {
    const feature = featureAt(e);
    const id = feature?.layer.id;
    if (feature && (id === 'bus-routes' || id === 'bus-stops')) {
      setPopup({ feature, lngLat: [e.lngLat.lng, e.lngLat.lat] });
      setHover(null);
    } else {
      setPopup(null);
    }
  };

  const highlightedRoute = popup?.feature.layer.id === 'bus-routes' ? (popup.feature.properties.route_id as string) : null;
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
            <Source id="roads" type="geojson" data={data.roads}>
              <Layer {...L.roadsLayer} beforeId={below} />
            </Source>
            <Source id="districts" type="geojson" data={data.districts}>
              <Layer {...L.districtsLayer} beforeId={below} />
            </Source>
            <Source id="taipei" type="geojson" data={data.taipei_boundary}>
              <Layer {...L.taipeiBoundaryLayer} beforeId={below} />
            </Source>
            <Source id="bus-routes" type="geojson" data={data.bus_routes}>
              <Layer {...withVisibility(L.busRoutesLayer, below)} />
              <Layer {...L.busRouteHighlightLayer(highlightedRoute)} beforeId={below} />
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
          <Popup longitude={popup.lngLat[0]} latitude={popup.lngLat[1]} offset={8} onClose={() => setPopup(null)} closeOnClick={false}>
            <FeaturePopup feature={popup.feature} />
          </Popup>
        )}
      </Map>

      {hover && (
        <div className="tooltip" style={{ left: hover.x, top: hover.y }}>
          <FeatureTooltip feature={hover.feature} grid={grid} state={state} />
        </div>
      )}
    </>
  );
}
