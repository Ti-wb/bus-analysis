import type { MapGeoJSONFeature } from 'maplibre-gl';
import type { ReactNode } from 'react';
import { DAY_LABELS, PLACE_BY_CATEGORY, STATION_TYPES } from '../config';
import { distanceText } from '../coverage';
import { cellValue, formatCount, hourText } from '../data';
import type { AppState, Grid, PlaceProps, StationType } from '../types';

export interface PopupFeature {
  feature: MapGeoJSONFeature;
  lngLat: [number, number];
}

const Warn = ({ children }: { children: ReactNode }) => <span className="warn">{children}</span>;

function GridTooltip({ id, grid, state }: { id: number; grid: Grid; state: AppState }) {
  const cell = grid.cells[id];
  if (!cell) return null;
  const [, , stops, subroutes, unscheduled] = cell;
  const when = `${DAY_LABELS[state.day]} ${hourText(state.hour)}`;
  if (!subroutes) {
    return <><b>{when}</b><br />格內沒有公車站牌</>;
  }
  const value = cellValue(cell, state);
  return (
    <>
      <b>{when}</b><br />
      {value === null ? <Warn>格內附線都沒有班表（資料缺漏）</Warn> : <>計畫發車 <b>{formatCount(value)}</b> 班</>}<br />
      站牌 {stops} 個 · 附線 {subroutes} 條
      {value !== null && unscheduled > 0 && <><br /><Warn>{unscheduled} 條附線無班表，數值偏低</Warn></>}
    </>
  );
}

function PlaceInfo({ p }: { p: PlaceProps }) {
  return (
    <>
      <b>{p.name ?? '（未命名）'}</b><br />
      {PLACE_BY_CATEGORY[p.category]?.label ?? p.category} · {p.district ?? ''}
      {'planned_households' in p && <><br />規劃 {p.planned_households ?? '—'} 戶 · {p.progress ?? ''}</>}
      <br /><Warn>候選位置，非出入口、不代表人流</Warn>
    </>
  );
}

export function CoverageTooltip({ distances, state }: { distances: { metres: Record<StationType, number>; capped: number }; state: AppState }) {
  const { metres, capped } = distances;
  const text = (m: number) => (m >= capped ? `> ${distanceText(capped)}` : distanceText(m));
  const walk = Math.min(...[...state.walkTypes].map((t) => metres[t]));
  const reach = [
    state.coverage.has('walk') && state.walkTypes.size > 0 && walk <= state.walkRadius && `步行 ${distanceText(state.walkRadius)} 內`,
    state.coverage.has('ride') && metres.youbike <= state.rideRadius && `YouBike ${distanceText(state.rideRadius)} 內`,
  ].filter(Boolean).join('、');
  return (
    <>
      <b>最近站點（直線）</b><br />
      {STATION_TYPES.map((t) => `${t.label} ${text(metres[t.key])}`).join(' · ')}<br />
      {reach ? <>在{reach}</> : <span className="muted">不在目前的覆蓋範圍內</span>}
    </>
  );
}

/** Hover text; JSX escapes every value taken from the data. */
export function FeatureTooltip({ feature, grid, state }: { feature: MapGeoJSONFeature; grid: Grid; state: AppState }) {
  const p = feature.properties;
  switch (feature.layer.id) {
    case 'bus-stops':
      return <><b>{p.name}</b><br />公車站牌 · {p.route_count} 條路線</>;
    case 'mrt-stations':
      return <><b>{p.name}</b><br />軌道車站</>;
    case 'mrt-lines':
      return <b>{p.name}</b>;
    case 'bus-routes':
      return <><b>{p.name}</b><br />平日計畫發車 {p.weekday_departures ?? '無班表'} 班 · 點選查看</>;
    case 'places':
      return <PlaceInfo p={p as PlaceProps} />;
    case 'youbike':
      return (
        <>
          <b>{p.name ?? '（未命名）'}</b><br />
          YouBike 2.0 · {p.district ?? ''} · {p.dock_count ?? '—'} 格
          {p.is_active === false && <><br /><Warn>資料時間點暫停營運</Warn></>}
        </>
      );
    default:
      return <GridTooltip id={Number(feature.id)} grid={grid} state={state} />;
  }
}

export function FeaturePopup({ feature }: { feature: MapGeoJSONFeature }) {
  const p = feature.properties;
  if (feature.layer.id === 'bus-routes') {
    return (
      <>
        <h4>{p.name}</h4>
        平日計畫發車：{p.weekday_departures ?? '無班表資料'} 班<br />
        <span className="muted">主路線所有附線加總，不分方向</span>
      </>
    );
  }
  // Array properties come back from MapLibre as JSON strings.
  const routes: string[] = typeof p.routes === 'string' ? JSON.parse(p.routes) : (p.routes ?? []);
  return (
    <>
      <h4>{p.name}</h4>
      {routes.length} 條路線 · 平日計畫發車 {p.weekday_departures ?? '—'} 班
      <div className="routes">{routes.join('、')}</div>
    </>
  );
}
