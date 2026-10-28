import type { MapGeoJSONFeature } from 'maplibre-gl';
import type { ReactNode } from 'react';
import { ALL_DAY, DAY_LABELS, NEED_BANDS, NEED_WEIGHT_LABELS, PLACE_BY_CATEGORY, QUADRANTS, STATION_TYPES } from '../config';
import { distanceText } from '../coverage';
import { cellValue, formatCount, hourText } from '../data';
import { quadrantOf, type NeedResult } from '../need';
import { countText, percentText, secondsText } from '../regional';
import type { AppState, Grid, NeedGrid, PlaceProps, StationType, VillageProps } from '../types';

export interface PopupFeature {
  feature: MapGeoJSONFeature;
  lngLat: [number, number];
}

const Warn = ({ children }: { children: ReactNode }) => <span className="warn">{children}</span>;
const numberWithUnit = (value: unknown, unit: string) => typeof value === 'number' ? `${countText(value)} ${unit}` : '無資料';

function GridTooltip({ id, grid, state }: { id: number; grid: Grid; state: AppState }) {
  const cell = grid.cells[id];
  if (!cell) return null;
  const [, , stops, subroutes, unscheduled] = cell;
  const regional = Boolean(grid.metadata.scope_city_codes?.length);
  const when = `${DAY_LABELS[state.day]} ${hourText(state.hour)}`;
  if (!subroutes) {
    return <><b>{when}</b><br />格內沒有公車站牌</>;
  }
  const value = cellValue(cell, state);
  return (
    <>
      <b>{when}</b><br />
      {value === null ? <Warn>格內附線都沒有班表（資料缺漏）</Warn> : <>計畫發車 <b>{formatCount(value)}</b> 班</>}<br />
      站牌 {stops} 個 · {regional ? '路線方向' : '附線'} {subroutes} 組
      {regional && value !== null && <><br /><span className="muted">依起站時刻估算，非本站實際到站班次</span></>}
      {value !== null && unscheduled > 0 && <><br /><Warn>{unscheduled} 組{regional ? '班表缺漏或業者識別不明，計畫班次不完整' : '附線無班表，數值偏低'}</Warn></>}
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
export function FeatureTooltip({ feature, grid, state }: { feature: MapGeoJSONFeature; grid: Grid | null; state: AppState }) {
  const p = feature.properties;
  switch (feature.layer.id) {
    case 'bus-stops':
      return <><b>{p.name}</b><br />公車站牌 · {p.route_count} 條路線 · 點選查看</>;
    case 'regional-bus-stops':
      return <><b>{p.name}</b><br />新北公車站位 · {p.route_count} 條路線{p.is_leapfrog && ' · 含跳蛙公車'}<br />點選查看所在里人口與設施</>;
    case 'mrt-stations':
      return <><b>{p.name}</b><br />捷運站 · 點選查看</>;
    case 'mrt-lines':
      return <b>{p.name}</b>;
    case 'bus-routes':
      return <><b>{p.name}</b><br />平日計畫發車 {p.weekday_departures ?? '無班表'} 班 · 點選查看</>;
    case 'regional-bus-routes':
      return <><b>{p.name}</b><br />新北{p.is_leapfrog ? '跳蛙' : ''}公車 · {p.stop_count} 站<br /><Warn>站序連線示意，非實際行駛軌跡</Warn><br />點選查看來源時間</>;
    case 'villages-fill':
      return <VillageInfo p={p as VillageProps} compact />;
    case 'traffic-sections':
      return <TrafficInfo p={p} />;
    case 'bus-travel-times':
      return <TravelInfo p={p} state={state} />;
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
      return grid ? <GridTooltip id={Number(feature.id)} grid={grid} state={state} /> : null;
  }
}

export function VillageInfo({ p, compact = false }: { p: VillageProps; compact?: boolean }) {
  return <>
    <b>{p.city_name}{p.district_name}{p.name}</b><br />
    戶籍人口 {numberWithUnit(p.population, '人')} · {numberWithUnit(p.households, '戶')}<br />
    65 歲以上占比 {percentText(p.elderly_share)}<br />
    {!compact && <>0–14 歲 {numberWithUnit(p.population_age_0_14, '人')}<br />15–64 歲 {numberWithUnit(p.population_age_15_64, '人')}<br />65 歲以上 {numberWithUnit(p.population_age_65_plus, '人')}<br /></>}
    統計期 {p.period ?? '未提供'} · 里界 {p.boundary_period ?? '未提供'}
    {typeof p.population !== 'number' && <><br /><Warn>代碼與名稱未核對，或未提供人口；缺值非 0</Warn></>}
    {!compact && <><br /><span className="muted">整里統計，非站點 350 m 範圍人口</span></>}
  </>;
}

function TrafficInfo({ p }: { p: Record<string, unknown> }) {
  return <>
    <b>{String(p.road_name ?? p.name ?? '道路路段')}</b><br />
    速率 {typeof p.travel_speed === 'number' ? `${p.travel_speed} km/h` : '無可用數值'}<br />
    旅行時間 {secondsText(p.travel_time)}<br />
    快照 {String(p.data_collect_time ?? '來源未提供時間')}<br />
    <Warn>起迄點示意線，非即時路況</Warn>
  </>;
}

function TravelInfo({ p, state }: { p: Record<string, unknown>; state: AppState }) {
  return <>
    <b>{String(p.route_name ?? '公車站間旅時')}</b><br />
    {String(p.from_name ?? '')} → {String(p.to_name ?? '')}<br />
    {DAY_LABELS[state.day]} {hourText(state.hour)}：{state.hour === ALL_DAY
      ? typeof p.min_seconds !== 'number' ? '無可用數值' : `${secondsText(p.min_seconds)}～${secondsText(p.max_seconds)}（${p.available_hours} 個整點有值）`
      : secondsText(p.seconds)}<br />
    有效期 {String(p.effective_date ?? '未提供')}～{String(p.expire_date ?? '未提供')}<br />
    <Warn>歷史推估、站間示意線；週六與週日共用週末模型</Warn>
  </>;
}

export function RoutePopup({ feature, state }: { feature: MapGeoJSONFeature; state: AppState }) {
  const p = feature.properties;
  if (feature.layer.id === 'villages-fill') return <VillageInfo p={p as VillageProps} />;
  if (feature.layer.id === 'traffic-sections') return <TrafficInfo p={p} />;
  if (feature.layer.id === 'bus-travel-times') return <TravelInfo p={p} state={state} />;
  if (feature.layer.id === 'regional-bus-routes') return <>
    <h4>{p.name}</h4>
    {p.subroute_name && <p>{p.subroute_name}</p>}
    <p>新北{p.is_leapfrog ? '跳蛙' : ''}公車 · 方向 {p.direction} · {p.stop_count} 站</p>
    <p>來源時間 {p.source_updated_at ?? '未提供'}</p>
    <p className="muted">依站序連線示意，非行駛軌跡；未納入臺北市班次網格、覆蓋或需求落差。</p>
  </>;
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
