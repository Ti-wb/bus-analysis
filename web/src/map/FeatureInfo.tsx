import type { MapGeoJSONFeature } from 'maplibre-gl';
import type { ReactNode } from 'react';
import { DAY_LABELS, PLACE_BY_CATEGORY, STATION_TYPES } from '../config';
import { distanceText } from '../coverage';
import { cellValue, formatCount, hourText } from '../data';
import type { Nearby } from '../catchment';
import type { AppState, CatchmentValue, Grid, PlaceProps, StationType } from '../types';

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

export function RoutePopup({ feature }: { feature: MapGeoJSONFeature }) {
  const p = feature.properties;
  return (
    <>
      <h4>{p.name}</h4>
      平日計畫發車：{p.weekday_departures ?? '無班表資料'} 班<br />
      <span className="muted">主路線所有附線加總，不分方向</span>
    </>
  );
}

/** Route names of a stop; array properties come back from MapLibre as JSON strings. */
export function stopRoutes(feature: MapGeoJSONFeature): string[] {
  const routes = feature.properties.routes;
  return typeof routes === 'string' ? JSON.parse(routes) : (routes ?? []);
}

interface StopPopupProps {
  feature: MapGeoJSONFeature;
  radii: number[];
  radius: number;
  onRadius: (radius: number) => void;
  /** Population per radius; undefined while loading, null when the stop has no entry. */
  catchment: CatchmentValue[] | null | undefined;
  nearby: Nearby;
}

/** Walking catchment of a bus stop: residents, stations and places within the radius. */
export function StopPopup({ feature, radii, radius, onRadius, catchment, nearby }: StopPopupProps) {
  const p = feature.properties;
  const routes = stopRoutes(feature);
  const value = catchment?.[radii.indexOf(radius)];
  const [people, households, share] = value ?? [null, null, 0];
  const { mrt, youbike } = nearby;
  return (
    <div className="stop-popup">
      <h4>{p.name}</h4>
      <div className="segmented" role="radiogroup" aria-label="步行範圍半徑">
        {radii.map((r) => (
          <button key={r} role="radio" aria-checked={r === radius} className={r === radius ? 'is-active' : ''} onClick={() => onRadius(r)}>
            {distanceText(r)}
          </button>
        ))}
      </div>
      <dl>
        <dt>戶籍人口</dt>
        <dd>
          {catchment === undefined ? <span className="muted">載入中…</span>
            : people === null ? <span className="muted">範圍內沒有臺北市人口資料</span>
            : <><b>約 {people.toLocaleString()} 人</b> · {households!.toLocaleString()} 戶</>}
          {people !== null && share < 1 && <><br /><span className="warn">只有 {Math.round(share * 100)}% 範圍有臺北市資料，新北市未計</span></>}
        </dd>
        <dt>捷運</dt>
        <dd>{mrt.count ? <>{mrt.count} 站 · 最近 {mrt.nearest!.name} {mrt.nearest!.metres} m</> : <span className="muted">範圍內沒有車站</span>}</dd>
        <dt>YouBike</dt>
        <dd>{youbike.count ? <>{youbike.count} 站 · {youbike.docks} 格</> : <span className="muted">範圍內沒有站點</span>}</dd>
        <dt>其他公車站位</dt>
        <dd>{nearby.stops} 個</dd>
        <dt>周邊地點</dt>
        <dd>
          {nearby.places.length ? nearby.places.map((c) => (
            <span key={c.key} className="place-count"><i style={{ background: c.color }} />{c.label} {c.count}</span>
          )) : <span className="muted">沒有所選類別的地點</span>}
        </dd>
        <dt>這一站</dt>
        <dd>{routes.length} 條路線 · 平日計畫發車 {p.weekday_departures ?? '—'} 班</dd>
      </dl>
      <div className="routes">{routes.join('、')}</div>
      <p className="muted note">直線 {distanceText(radius)} 圓內。戶籍人口不是搭乘量；相鄰站牌範圍重疊，不可加總；地點依左側篩選類別計算，為候選標記、未去重。</p>
    </div>
  );
}
