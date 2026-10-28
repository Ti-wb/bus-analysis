import { useEffect, useMemo, useRef } from 'react';
import Map, { AttributionControl, Layer, NavigationControl, Source, type MapRef } from 'react-map-gl/maplibre';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import type { Feature, FeatureCollection } from 'geojson';
import type { ExpressionSpecification } from 'maplibre-gl';
import { BASE_STYLE, DEFAULT_VIEW } from '../config';
import type { MapData } from '../types';
import type { Ranked } from './types';
import { motionDuration } from '../motion';
import { LOCAL_STYLE, useMapRecovery } from '../map/useMapRecovery';
import MapNotice from '../map/MapNotice';
import { roadsLayer, districtsLayer } from '../map/layers';

const CANDIDATE = '#7b8c9a';
const PRIORITY = '#b76c30';
const SELECTION = '#1d559f';
const candidateColor: ExpressionSpecification = ['case', ['>=', ['get', 'score'], 80], PRIORITY, CANDIDATE];

export default function ImprovementMap({ rows, selected, map, onSelect }: { rows: Ranked[]; selected: Ranked | null; map: MapData; onSelect: (id: string) => void }) {
  const ref = useRef<MapRef>(null);
  const { mapLoad, dispatchMapLoad, fallbackView, retryMap } = useMapRecovery(ref);
  const ready = mapLoad.status === 'ready';
  const localBase = mapLoad.base === 'local';
  const fittedSelection = useRef<string | null>(null);
  const data = useMemo<FeatureCollection>(() => ({ type: 'FeatureCollection', features: rows.map((r): Feature => ({
    type: 'Feature', properties: { id: r.id, name: r.name, score: r.score },
    geometry: r.path && r.path.length > 1 ? { type: 'LineString', coordinates: r.path }
      : r.bounds ? { type: 'Polygon', coordinates: [[[r.bounds[0][0], r.bounds[0][1]], [r.bounds[1][0], r.bounds[0][1]], r.bounds[1], [r.bounds[0][0], r.bounds[1][1]], r.bounds[0]]] }
      : { type: 'Point', coordinates: r.at },
  })) }), [rows]);
  useEffect(() => {
    if (!selected) { fittedSelection.current = null; return; }
    if (!ready || fittedSelection.current === selected.id) return;
    fittedSelection.current = selected.id;
    const pts = selected.path?.length ? selected.path : selected.bounds ?? [selected.at];
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const compact = window.innerWidth <= 900;
    ref.current?.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]],
      { absolutePadding: true, padding: { top: compact ? 140 : 60, left: 40, bottom: 60, right: compact ? 40 : 560 }, duration: motionDuration(500), maxZoom: pts.length === 1 ? 15.5 : 14.5 });
  }, [ready, selected]);
  const selectedData = useMemo<FeatureCollection>(() => ({ type: 'FeatureCollection', features: data.features.filter((f) => f.properties?.id === selected?.id) }), [data, selected?.id]);
  const view = fallbackView ?? DEFAULT_VIEW;
  return <div className={`im-map${selected ? ' has-selection' : ''}`}>
    <Map key={mapLoad.attempt} ref={ref} initialViewState={{ ...view, longitude: view.center[0], latitude: view.center[1], zoom: fallbackView?.zoom ?? 11.8 }} mapStyle={localBase ? LOCAL_STYLE : BASE_STYLE}
      workerUrl={workerUrl} attributionControl={false} localIdeographFontFamily="'Noto Sans TC', 'PingFang TC', 'Microsoft JhengHei', sans-serif"
      onLoad={({ target: instance }) => {
        instance.getCanvas().setAttribute('aria-label', '改善清單地圖；方向鍵移動，切換清單視角可選取項目');
        for (const layer of instance.getStyle().layers) {
          if (layer.type === 'symbol' && JSON.stringify(instance.getLayoutProperty(layer.id, 'text-field') ?? '').includes('name')) {
            instance.setLayoutProperty(layer.id, 'text-field', ['coalesce', ['get', 'name:zh-Hant'], ['get', 'name:nonlatin'], ['get', 'name']]);
          }
        }
        dispatchMapLoad({ type: 'loaded', attempt: mapLoad.attempt });
      }} onError={(event) => dispatchMapLoad({ type: event.target ? 'resource-error' : 'initialization-error', attempt: mapLoad.attempt })}
      interactiveLayerIds={ready ? ['im-candidate-lines', 'im-candidate-points', 'im-candidate-blocks'] : []}
      onClick={(e) => { const id = e.features?.[0]?.properties?.id; if (typeof id === 'string') onSelect(id); }} cursor="pointer">
      {ready && <>
      {localBase && <>
        <Source id="im-roads" type="geojson" data={map.roads}><Layer {...roadsLayer} /></Source>
        <Source id="im-districts" type="geojson" data={map.districts}><Layer {...districtsLayer} /></Source>
      </>}
      <Source id="im-mrt" type="geojson" data={map.mrt_lines}><Layer id="im-mrt-lines" type="line" paint={{ 'line-color': '#a6b0b7', 'line-width': 1.5, 'line-opacity': .4 }} /></Source>
      <Source id="im-candidates" type="geojson" data={data}>
        <Layer id="im-candidate-blocks" type="fill" filter={['==', '$type', 'Polygon']} layout={{ 'fill-sort-key': ['get', 'score'] }}
          paint={{ 'fill-color': [...candidateColor], 'fill-opacity': selected ? .08 : ['case', ['>=', ['get', 'score'], 80], .3, .12], 'fill-outline-color': [...candidateColor] }} />
        <Layer id="im-candidate-lines" type="line" filter={['==', '$type', 'LineString']} layout={{ 'line-sort-key': ['get', 'score'] }}
          paint={{ 'line-color': [...candidateColor], 'line-width': ['case', ['>=', ['get', 'score'], 80], 2.6, 1.2], 'line-opacity': selected ? .12 : ['case', ['>=', ['get', 'score'], 80], .88, .28] }} />
        <Layer id="im-candidate-points" type="circle" filter={['==', '$type', 'Point']} layout={{ 'circle-sort-key': ['get', 'score'] }}
          paint={{ 'circle-color': [...candidateColor], 'circle-radius': ['case', ['>=', ['get', 'score'], 80], 6, 4], 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5, 'circle-opacity': selected ? .3 : .9, 'circle-stroke-opacity': selected ? .3 : 1 }} />
      </Source>
      <Source id="im-selection" type="geojson" data={selectedData}>
        <Layer id="im-selected-casing" type="line" filter={['!=', '$type', 'Point']} paint={{ 'line-color': '#ffffff', 'line-width': 8 }} />
        <Layer id="im-selected-line" type="line" filter={['!=', '$type', 'Point']} paint={{ 'line-color': SELECTION, 'line-width': 4 }} />
        <Layer id="im-selected-point" type="circle" filter={['==', '$type', 'Point']} paint={{ 'circle-color': SELECTION, 'circle-radius': 9, 'circle-stroke-color': '#fff', 'circle-stroke-width': 3 }} />
      </Source>
      </>}
      <NavigationControl position="bottom-left" showCompass={false} /><AttributionControl position="bottom-right" compact customAttribution="公車：臺北市公共運輸處" />
    </Map>
    <div className="im-map-legend"><b>{rows.length.toLocaleString()} 個符合條件項目</b><span><i style={{ background: PRIORITY }} />高優先 ≥80 <i style={{ background: CANDIDATE }} />其他候選 <i style={{ background: SELECTION }} />目前選取</span><small>分數含 Mock Data · 點擊路線、站位或區塊檢視</small>
      {rows[0]?.kind === 'routes' && <small>各路線顯示代表附線方向；缺線形以站序連線示意。</small>}
      {selected?.kind === 'routes' && <small>選取：{selected.name} · {selected.profile_direction} · {selected.path_is_stop_connection ? '站序示意連線' : '來源線形'}</small>}
    </div>
    <MapNotice state={mapLoad} onRetry={retryMap} onDismiss={() => dispatchMapLoad({ type: 'dismiss', attempt: mapLoad.attempt })} retained="目前篩選與選取仍保留。" />
    {mapLoad.status === 'loading' && <div className="loading" role="status">載入改善清單地圖中…</div>}
  </div>;
}
