import { useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react';
import type { MapRef } from 'react-map-gl/maplibre';
import StationAge from './StationAge';
import { nearby } from '../catchment';
import { loadCatchments } from '../data';
import { useData } from '../useData';
import { countText, percentText, villageAt } from '../regional';
import { STATION_LABELS, STATION_RADIUS } from '../stations';
import type { MapData, StationSelection, StationType } from '../types';

export function StationIcon({ type }: { type: StationType }) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {type === 'youbike' ? <>
        <circle cx="5" cy="16" r="4" /><circle cx="19" cy="16" r="4" />
        <path d="m5 16 5-9 5 9H5m3-9h4m-2 0 9 9-4-12h3" />
      </> : <>
        <rect x="5" y="3" width="14" height="16" rx={type === 'mrt' ? 5 : 2} />
        <path d="M5 7h14M5 13h14M8 19l-2 3m10-3 2 3" />
        <path d="M8 16h1m6 0h1" />
      </>}
    </svg>
  );
}

const AGE_LABELS: Record<string, string> = { '0-14': '0–14 歲', '15-24': '15–24 歲', '25-64': '25–64 歲', '65_plus': '65 歲以上' };

/** Age groups as share and head count; groups follow the catchment file's own order. */
function AgeGroups({ groups, counts, total }: { groups: string[]; counts: number[]; total: number }) {
  return (
    <ul className="station-age-grid">
      {groups.map((group, k) => (
        <li key={group}>
          <span>{AGE_LABELS[group] ?? group}</span>
          <b>{total ? Math.round((counts[k] / total) * 100) : 0}%</b>
          <small>{counts[k].toLocaleString('zh-TW')} 人</small>
        </li>
      ))}
    </ul>
  );
}

interface Props {
  mode?: 'popup' | 'focus';
  station: StationSelection;
  mapRef: RefObject<MapRef | null>;
  mapReady?: boolean;
  data: MapData;
  placeKeys: Set<string>;
  onClose: () => void;
}

/** The Focus card docks beside the overview; ordinary station cards follow their marker. */
export default function StationInfo({ mode = 'popup', station, mapRef, mapReady = true, data, placeKeys, onClose }: Props) {
  const cardRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [mobileExpanded, setMobileExpanded] = useState(false);
  const regionalBus = station.type === 'bus' && Boolean(station.properties.source_scope);

  const { data: catchments, error: failed, retry, loading } = useData(loadCatchments, !regionalBus);

  useEffect(() => {
    closeRef.current?.focus({ preventScroll: true });
    setExpanded(false);
    setMobileExpanded(false);
  }, [station.type, station.id, regionalBus]);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      // An open native dialog (e.g. Data & methods) owns its own Escape key.
      if (event.key === 'Escape' && !(event.target as HTMLElement).closest('dialog')) {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [onClose]);

  useLayoutEffect(() => {
    if (!mapReady) return;
    const card = cardRef.current;
    const map = mapRef.current;
    if (!card || !map) return;
    if (mode === 'focus') {
      card.style.left = '';
      card.style.top = '';
      card.style.transform = '';
      return;
    }
    const container = map.getContainer();
    card.style.left = card.style.top = '0';
    let width = 0, height = 0, cardWidth = 0, cardHeight = 0;
    const position = () => {
      const { x, y } = map.project(station.at);
      const margin = 12;
      const gap = 28;
      // Prefer the side with room; clamp on narrow screens without changing the view.
      const preferred = x + gap + cardWidth <= width - margin ? x + gap : x - gap - cardWidth;
      const left = Math.max(margin, Math.min(preferred, width - cardWidth - margin));
      const top = Math.max(margin, Math.min(y - cardHeight / 3, height - cardHeight - margin));
      card.style.transform = `translate3d(${left}px, ${top}px, 0)`;
    };
    // Dimensions change on resize/content updates, not on every camera frame.
    const measure = () => {
      width = container.clientWidth; height = container.clientHeight;
      cardWidth = card.offsetWidth; cardHeight = card.offsetHeight;
      position();
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(card);
    observer.observe(container);
    map.on('move', position);
    return () => { observer.disconnect(); map.off('move', position); };
  }, [mapRef, mapReady, station, mode]);

  const surroundings = useMemo(
    () => nearby(data, station.at, STATION_RADIUS, station.type === 'bus' ? station.id : null, placeKeys),
    [data, station, placeKeys],
  );
  const value = !regionalBus ? catchments?.records[station.type]?.[station.id]?.[catchments.radii.indexOf(STATION_RADIUS)] : undefined;
  const [people, households, share, ages] = value ?? [null, null, 0, null];
  const catchmentAgeGroups = catchments?.age_groups;
  const facilities = expanded ? surroundings.facilities : surroundings.facilities.slice(0, 3);
  const period = catchments?.metadata.population_period.end;
  const agePeriod = catchments?.metadata.age_period?.end ?? period;
  const village = useMemo(() => villageAt(data.villages, station.at), [data.villages, station.at]);
  const villageAgeGroups = village ? [
    { label: '0–14 歲', value: village.population_age_0_14, color: '#337cda' },
    { label: '15–64 歲', value: village.population_age_15_64, color: '#639a70' },
    { label: '65 歲以上', value: village.population_age_65_plus, color: '#be7d52' },
  ] : [];

  return (
    <section ref={cardRef} className={`station-card station-${station.type}${mode === 'focus' ? ` is-focus${mobileExpanded ? '' : ' is-compact'}` : ''}`} role="dialog" aria-modal="false" aria-labelledby="station-title" aria-describedby="station-scope">
      <header className="station-header">
        <div className="station-kind"><span className="station-icon"><StationIcon type={station.type} /></span>{STATION_LABELS[station.type]}</div>
        <button ref={closeRef} className="station-close" onClick={onClose} aria-label="關閉站點資訊">
          <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" /></svg>
        </button>
        <h2 id="station-title">{station.properties.name ?? '未命名站點'}</h2>
        <p id="station-scope">{regionalBus ? '新北公車站位 · ' : ''}周邊 {STATION_RADIUS} m 設施與所在里人口</p>
        {mode === 'focus' && <button className="station-details-toggle" aria-expanded={mobileExpanded} aria-controls="station-focus-details" onClick={() => setMobileExpanded((value) => !value)}>{mobileExpanded ? '收合周邊資訊' : '查看人口與設施'}<span aria-hidden="true">{mobileExpanded ? '⌃' : '⌄'}</span></button>}
      </header>

      <div id="station-focus-details" className="station-focus-details">
      <section className="station-population" aria-busy={loading} aria-live="polite">
        <div className="station-section-head"><h3>周邊 {STATION_RADIUS} m 統計區人口</h3><span className="station-badge">臺北戶籍估算</span></div>
        {loading ? <p className="station-empty-title">人口資料載入中…</p>
          : regionalBus ? <p className="station-empty">此來源未提供新北站位的 {STATION_RADIUS} m 統計區人口估算；下方另列整里統計與村里年齡分攤。</p>
          : failed ? <div className="station-empty"><b>人口資料載入失敗</b><p>設施資訊仍可查閱。</p><button className="station-text-button" onClick={retry}>重新載入</button></div>
            : people === null ? <div className="station-empty"><b>尚無人口資料</b><p>此範圍尚無可用的臺北市戶籍人口資料。</p></div>
              : <><p className="station-population-value">{people.toLocaleString('zh-TW')}<span>人</span></p>
                <p className="station-meta">約 {households?.toLocaleString('zh-TW') ?? '—'} 戶 · {period} 戶籍人口</p>
                {share < 1 && <p className="station-warning">僅 {Math.round(share * 100)}% 範圍有臺北市資料，其餘未計。</p>}
              </>}
        <h3 className="station-age-title">統計區年齡分布（{regionalBus ? '此站不適用' : agePeriod ?? '期間未知'}）</h3>
        {regionalBus ? <div className="station-empty station-age-empty"><p>此來源尚無新北站位的 {STATION_RADIUS} m 統計區年齡估算</p><small>下方整里統計與村里年齡分攤各自保留來源，不混用統計區資料。</small></div>
          : loading || failed ? null
            : people !== null && ages && catchmentAgeGroups ? <>
              <p className="station-meta">{catchments?.metadata.age_period?.end ?? '統計期未提供'} · 臺北統計區面積分攤</p>
              <AgeGroups groups={catchmentAgeGroups} counts={ages} total={people} /><p className="station-meta">比例以本卡已涵蓋的統計區人口為分母；未涵蓋範圍另計。</p>
            </>
              : <div className="station-empty station-age-empty"><p>暫無可用的年齡分布</p><small>{people === null ? '此範圍沒有人口資料' : '範圍內有統計區缺年齡資料'}</small></div>}
        <p className="station-warning">{regionalBus ? '上方統計區來源未涵蓋此新北站位；下方村里資料各自保留來源與月份，不混算。' : '此卡使用最小統計區人口；與下方村里年齡資料來源、月份不同，總數不可直接比較或相加。'}</p>
      </section>

      <section className="station-village">
        <div className="station-section-head"><h3>站點所在里 · 人口與年齡</h3><span>{village?.period ?? '統計期未提供'}</span></div>
        {village ? <>
          <p className="station-village-name">{village.city_name}{village.district_name}{village.name}</p>
          <p className="station-village-total">{countText(village.population)}<span>{village.population !== null ? ' 人' : ''} · {countText(village.households)}{village.households !== null ? ' 戶' : ''}</span></p>
          {village.population === null && <p className="station-warning">里界名稱與人口資料尚未核對，或此里未提供數值；缺值非 0。</p>}
          {villageAgeGroups.map((group) => <div className="station-age-row" key={group.label}>
            <span><i style={{ background: group.color }} />{group.label}</span>
            <b>{countText(group.value)}{group.value !== null ? ' 人' : ''}</b>
          </div>)}
          <p className="station-meta">65 歲以上占比 {percentText(village.elderly_share)} · 里界 {village.boundary_period ?? '版本未提供'}</p>
          <p className="station-warning">整里原始戶籍統計，非本站 {STATION_RADIUS} m 人口或年齡估算。</p>
        </> : <p className="station-empty">{data.villages ? '此點未落在已整理的里界內。' : '里人口圖層尚未載入；可在圖層面板檢查或重試。'}</p>}
      </section>

      <StationAge station={station} />

      <section className="station-facilities">
        <div className="station-section-head"><h3>附近重要設施</h3><span>{STATION_RADIUS} m 內</span></div>
        {facilities.length ? <ul>
          {facilities.map((f) => <li key={f.id}><span className="station-facility-kind">{f.label}</span><span className="station-facility-name">{f.name}</span><span className="station-distance">約 {f.metres} m</span></li>)}
        </ul> : <p className="station-empty">範圍內沒有目前所選類別的設施。</p>}
        {surroundings.facilities.length > 3 && <button className="station-text-button" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>{expanded ? '收合設施' : `查看全部 ${surroundings.facilities.length} 處設施`}</button>}
      </section>

      <details className="station-more" key={`${station.type}:${station.id}`}>
        <summary>站點與周邊交通</summary>
        {station.type === 'bus' && <><p>{station.properties.route_count} 條路線{!regionalBus && <> · 平日計畫發車 {station.properties.weekday_departures ?? '—'} 班</>}</p><p className="station-routes">{station.properties.routes.join('、') || '尚無路線資料'}</p>{regionalBus && <p className="muted">新北路網依站序連線，尚未納入臺北班次、覆蓋及需求模型。來源時間 {station.properties.source_updated_at ?? '未提供'}。</p>}</>}
        {station.type === 'mrt' && <p>以車站點位估算，並非各出入口的步行範圍。</p>}
        {station.type === 'youbike' && <><p>{station.properties.district ?? '行政區未提供'} · {station.properties.dock_count ?? '—'} 個停車格</p><p>{station.properties.is_active === false ? '資料時間點暫停營運' : station.properties.is_active === true ? '資料時間點營運中' : '營運狀態未提供'}；無即時可借車數。</p></>}
        <p>{station.type === 'bus' ? '其他公車站位' : '公車站位'} {surroundings.stops} 個 · 捷運 {surroundings.mrt.count} 站 · YouBike {surroundings.youbike.count} 站</p>
        {station.type !== 'mrt' && surroundings.mrt.nearest && <p>最近捷運：{surroundings.mrt.nearest.name} · 約 {surroundings.mrt.nearest.metres} m</p>}
        {surroundings.youbike.count > 0 && <p>範圍內 YouBike 停車格：{surroundings.youbike.docks?.toLocaleString('zh-TW') ?? '資料不完整'}；無即時可借車數。</p>}
        <p className="muted">同類站點統計含本站（公車站位除外）。{station.type === 'bus' && '公車計畫班次不代表本站到站時刻。'}</p>
        <p className="muted">{!regionalBus && <>上方 350 m 統計區人口與其年齡分布依統計區面積分攤，相鄰範圍不可加總；比例四捨五入，合計可能不是 100%。{catchments && <>人口 {period}、邊界 {catchments.metadata.boundary_vintage} 年。</>}</>}設施依地點篩選，為未去重的候選位置，非出入口。</p>
      </details>

      <footer className="station-note">戶籍人口估算，非搭乘量 · 整里原始統計另列 · 設施距離採直線估算</footer>
      </div>
    </section>
  );
}
