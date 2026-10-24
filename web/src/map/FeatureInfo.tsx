import type { MapGeoJSONFeature } from 'maplibre-gl';
import type { ReactNode } from 'react';
import { DAY_LABELS, NEED_BANDS, NEED_WEIGHT_LABELS, PLACE_BY_CATEGORY, QUADRANTS, STATION_TYPES } from '../config';
import { distanceText } from '../coverage';
import { cellValue, formatCount, hourText } from '../data';
import { quadrantOf, type NeedResult } from '../need';
import type { AppState, Grid, NeedGrid, PlaceProps, StationType } from '../types';

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
  const walkable = STATION_TYPES.filter((t) => state.walkTypes.has(t.key) && metres[t.key] <= state.walkRadii[t.key]);
  const reach = [
    state.coverage.has('walk') && walkable.length > 0 && `步行到${walkable.map((t) => t.label).join('、')}站範圍內`,
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
      return <><b>{p.name}</b><br />公車站牌 · {p.route_count} 條路線 · 點選查看</>;
    case 'mrt-stations':
      return <><b>{p.name}</b><br />捷運站 · 點選查看</>;
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
          YouBike 2.0 · {p.district ?? ''} · {p.dock_count ?? '—'} 格<br />點選查看站點資訊
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

const pct = (r: number) => `${Math.round(r * 100)}`;

/** Breakdown of one transport-need cell: demand components, supply and raw inputs. */
export function NeedTooltip({ i, grid, result, state }: { i: number; grid: NeedGrid; result: NeedResult; state: AppState }) {
  const c = grid.cells;
  const v = grid.values[state.band];
  const band = NEED_BANDS.find((b) => b.key === state.band)!;
  const head = <><b>{c.district[i] ?? ''} · {band.label}</b><br /></>;
  if (!c.in_scope[i]) {
    return <>{head}人口 {c.population[i]} 人，附近沒有主要地點<br /><span className="muted-light">不在分析範圍，不排名</span></>;
  }
  const quadrant = quadrantOf(result.rDemand[i], result.rSupply[i])!;
  const total = state.weights.reduce((a, b) => a + b, 0) || 1;
  const parts = [c.r_pop[i]!, v.r_poi[i]!, v.r_rail[i]!].map((r, k) => (r * state.weights[k]) / total);
  const gap = result.gap[i];
  return (
    <>
      {head}
      <b className="need-quadrant">{QUADRANTS[quadrant].label}</b>
      <span className="muted-light"> · 落差 {gap > 0 ? '+' : ''}{pct(gap)}</span>
      <div className="need-bars">
        <span>需求排名</span>
        <div className="need-bar">
          {parts.map((p, k) => <i key={k} className={`need-part-${k}`} style={{ width: `${p * 100}%` }} title={NEED_WEIGHT_LABELS[k]} />)}
        </div>
        <b>{pct(result.rDemand[i])}</b>
        <span>供給排名</span>
        <div className="need-bar"><i className="need-supply" style={{ width: `${result.rSupply[i] * 100}%` }} /></div>
        <b>{pct(result.rSupply[i])}</b>
      </div>
      <div className="need-legend-mini">
        {NEED_WEIGHT_LABELS.map((label, k) => <span key={label}><i className={`need-part-${k}`} />{label} {pct([c.r_pop[i]!, v.r_poi[i]!, v.r_rail[i]!][k])}</span>)}
      </div>
      人口 {c.population[i].toLocaleString()} 人 · 地點分數 {v.poi[i].toFixed(1)} · 捷運活動 {Math.round(v.rail[i]).toLocaleString()} 人次／時<br />
      公車加權 {v.f[i].toFixed(1)} 班／時 · 捷運鄰近度 {pct(c.s_metro[i])}%
      {c.partial[i] === 1 && <><br /><Warn>附近有附線缺班表，供給是下限</Warn></>}
    </>
  );
}
