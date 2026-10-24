import { Layer, Marker, Source } from 'react-map-gl/maplibre';
import type { FeatureCollection, Polygon } from 'geojson';
import { routePath } from './engine';
import type { Coordinate, Route, ScenarioData } from './types';

export interface EditorMapProps {
  data: ScenarioData;
  grid: FeatureCollection<Polygon> | null;
  route: Route | null;
  original: Route | null;
  deleting: boolean;
  showStopNumbers: boolean;
  interaction: 'select' | 'stops' | 'path';
  gridLines: boolean;
  bike: boolean;
  onStop: (id: string) => void;
  onRoute: (routeId: string) => void;
  onPath: (path: Coordinate[]) => void;
}

export function ScenarioGrid({ editor, beforeId }: { editor: EditorMapProps; beforeId?: string }) {
  if (!editor.grid) return null;
  return <Source id="scenario-grid" type="geojson" data={editor.grid}>
    {editor.bike && <Layer id="scenario-bike" type="fill" beforeId={beforeId} filter={['==', ['get', 'bike'], true]}
      paint={{ 'fill-color': '#88ceca', 'fill-opacity': .24 }} />}
    <Layer id="scenario-walk" type="fill" beforeId={beforeId} filter={['==', ['get', 'walk'], true]}
      paint={{ 'fill-color': ['case', ['==', ['get', 'frequency'], 0], '#e4dfd5',
        ['step', ['get', 'frequency'], '#ffe3c6', 4, '#fbc48d', 8, '#f5a459', 16, '#ed8733', 24, '#d76b20']], 'fill-opacity': .46 }} />
    <Layer id="scenario-grid-lines" type="line" beforeId={beforeId} layout={{ visibility: editor.gridLines ? 'visible' : 'none' }}
      paint={{ 'line-color': '#a0b4ba', 'line-opacity': .25, 'line-width': .35 }} />
    <Layer id="scenario-gain" type="line" beforeId={beforeId} filter={['==', ['get', 'increased'], true]}
      paint={{ 'line-color': '#337cda', 'line-width': 1, 'line-opacity': .85 }} />
    <Layer id="scenario-loss" type="fill" beforeId={beforeId} filter={['==', ['get', 'decreased'], true]}
      paint={{ 'fill-pattern': 'scenario-hatch', 'fill-opacity': .85 }} />
  </Source>;
}

export function RouteLayers({ editor, beforeId }: { editor: EditorMapProps; beforeId?: string }) {
  const { route, original, data, interaction, deleting } = editor;
  if (!route) return null;
  const path = routePath(route, data);
  const old = original && routePath(original, data);
  const line = (coordinates: Coordinate[]): FeatureCollection => ({ type: 'FeatureCollection', features: coordinates.length < 2 ? [] : [
    { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates } },
  ] });
  const oldStops = new Set(original?.stop_ids);
  const removed = original?.stop_ids.filter((id) => !route.stop_ids.includes(id)) ?? [];
  return <>
    {old && <Source id="scenario-original" type="geojson" data={line(old)}>
      <Layer id="scenario-original-line" type="line" beforeId={beforeId} paint={{ 'line-color': '#627e91', 'line-width': 3, 'line-opacity': .6, 'line-dasharray': [2, 2] }} />
    </Source>}
    <Source id="scenario-route" type="geojson" data={line(path)}>
      <Layer id="scenario-route-casing" type="line" beforeId={beforeId} paint={{ 'line-color': '#ffffff', 'line-width': 7 }} />
      <Layer id="scenario-route-line" type="line" beforeId={beforeId} paint={{ 'line-color': deleting ? '#be4966' : '#337cda', 'line-width': 3.5, ...(deleting ? { 'line-dasharray': [2, 2] } : {}) }} />
    </Source>
    {route.stop_ids.map((id, i) => {
      const s = data.records.stops[id];
      return <Marker key={`${id}:${i}`} longitude={s.at[0]} latitude={s.at[1]}>
        {editor.showStopNumbers ? <button className={`route-map-stop${deleting ? ' is-removed' : oldStops.has(id) ? ' is-existing' : ''}`}
          aria-label={`${i + 1} ${s.name}${interaction === 'stops' ? '，定位停靠站' : ''}`} title={`${i + 1}. ${s.name}`}
          onClick={(e) => { e.stopPropagation(); document.getElementById(`route-stop-${i}`)?.scrollIntoView({ block: 'nearest' }); }}>
          {deleting ? '×' : i + 1}
        </button> : <span className="route-map-dot" />}
      </Marker>;
    })}
    {removed.map((id, i) => <Marker key={`removed:${i}`} longitude={data.records.stops[id].at[0]} latitude={data.records.stops[id].at[1]}>
      <span className="route-map-stop is-removed" title={`取消停靠：${data.records.stops[id].name}`}>×</span>
    </Marker>)}
    {interaction === 'path' && path.map((at, i) => <Marker key={`vertex:${i}`} longitude={at[0]} latitude={at[1]} draggable
      onDragEnd={(e) => editor.onPath(path.map((p, j) => j === i ? [e.lngLat.lng, e.lngLat.lat] : p))}>
      <button className="route-vertex" aria-label={`路徑控制點 ${i + 1}；Delete 移除`} title="拖曳調整；Delete 移除"
        onClick={(e) => e.stopPropagation()} onKeyDown={(e) => {
          if ((e.key === 'Delete' || e.key === 'Backspace') && path.length > 2) { e.preventDefault(); editor.onPath(path.filter((_, j) => i !== j)); }
          const delta = .0001;
          if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
            e.preventDefault(); e.stopPropagation();
            const next: Coordinate = [at[0] + (e.key === 'ArrowLeft' ? -delta : e.key === 'ArrowRight' ? delta : 0), at[1] + (e.key === 'ArrowDown' ? -delta : e.key === 'ArrowUp' ? delta : 0)];
            editor.onPath(path.map((p, j) => j === i ? next : p));
          }
        }} />
    </Marker>)}
    {interaction === 'path' && path.slice(1).map((at, i) => <Marker key={`mid:${i}`} longitude={(path[i][0] + at[0]) / 2} latitude={(path[i][1] + at[1]) / 2}>
      <button className="route-midpoint" title="新增路徑控制點" aria-label={`在路徑第 ${i + 1} 段新增控制點`} onClick={(e) => {
        e.stopPropagation(); editor.onPath([...path.slice(0, i + 1), [(path[i][0] + at[0]) / 2, (path[i][1] + at[1]) / 2], ...path.slice(i + 1)]);
      }}>+</button>
    </Marker>)}
  </>;
}
