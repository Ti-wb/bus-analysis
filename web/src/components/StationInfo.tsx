import { useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react';
import type { MapRef } from 'react-map-gl/maplibre';
import { nearby } from '../catchment';
import { loadCatchments } from '../data';
import { STATION_LABELS, STATION_RADIUS } from '../stations';
import type { MapData, StationCatchments, StationSelection, StationType } from '../types';

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

interface Props {
  mode?: 'popup' | 'focus';
  station: StationSelection;
  mapRef: RefObject<MapRef | null>;
  data: MapData;
  placeKeys: Set<string>;
  onClose: () => void;
}

/** The Focus card docks beside the overview; ordinary station cards follow their marker. */
export default function StationInfo({ mode = 'popup', station, mapRef, data, placeKeys, onClose }: Props) {
  const cardRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [catchments, setCatchments] = useState<StationCatchments | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [mobileExpanded, setMobileExpanded] = useState(false);

  useEffect(() => {
    let current = true;
    loadCatchments().then((value) => {
      if (current) { setCatchments(value); setFailed(false); }
    }, () => { if (current) setFailed(true); });
    return () => { current = false; };
  }, [attempt]);

  useEffect(() => {
    closeRef.current?.focus({ preventScroll: true });
    setExpanded(false);
    setMobileExpanded(false);
  }, [station.type, station.id]);

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
    const card = cardRef.current;
    const map = mapRef.current;
    if (!card || !map) return;
    if (mode === 'focus') {
      card.style.left = '';
      card.style.top = '';
      return;
    }
    const container = map.getContainer();
    const position = () => {
      const { x, y } = map.project(station.at);
      const width = container.clientWidth;
      const height = container.clientHeight;
      const margin = 12;
      const gap = 28;
      const cardWidth = card.offsetWidth;
      const cardHeight = card.offsetHeight;
      // Prefer the side with room; clamp on narrow screens without changing the view.
      const preferred = x + gap + cardWidth <= width - margin ? x + gap : x - gap - cardWidth;
      card.style.left = `${Math.max(margin, Math.min(preferred, width - cardWidth - margin))}px`;
      card.style.top = `${Math.max(margin, Math.min(y - cardHeight / 3, height - cardHeight - margin))}px`;
    };
    position();
    const observer = new ResizeObserver(position);
    observer.observe(card);
    observer.observe(container);
    map.on('move', position);
    return () => { observer.disconnect(); map.off('move', position); };
  }, [mapRef, station, mode]);

  const surroundings = useMemo(
    () => nearby(data, station.at, STATION_RADIUS, station.type === 'bus' ? station.id : null, placeKeys),
    [data, station, placeKeys],
  );
  const value = catchments?.records[station.type]?.[station.id]?.[catchments.radii.indexOf(STATION_RADIUS)];
  const [people, households, share] = value ?? [null, null, 0];
  const loading = !catchments && !failed;
  const facilities = expanded ? surroundings.facilities : surroundings.facilities.slice(0, 3);
  const period = catchments?.metadata.population_period.end;

  return (
    <section ref={cardRef} className={`station-card station-${station.type}${mode === 'focus' ? ` is-focus${mobileExpanded ? '' : ' is-compact'}` : ''}`} role="dialog" aria-modal="false" aria-labelledby="station-title" aria-describedby="station-scope">
      <header className="station-header">
        <div className="station-kind"><span className="station-icon"><StationIcon type={station.type} /></span>{STATION_LABELS[station.type]}</div>
        <button ref={closeRef} className="station-close" onClick={onClose} aria-label="關閉站點資訊">
          <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" /></svg>
        </button>
        <h2 id="station-title">{station.properties.name ?? '未命名站點'}</h2>
        <p id="station-scope">站點周邊 {STATION_RADIUS} m · 居住人口概況</p>
        {mode === 'focus' && <button className="station-details-toggle" aria-expanded={mobileExpanded} aria-controls="station-focus-details" onClick={() => setMobileExpanded((value) => !value)}>{mobileExpanded ? '收合周邊資訊' : '查看人口與設施'}<span aria-hidden="true">{mobileExpanded ? '⌃' : '⌄'}</span></button>}
      </header>

      <div id="station-focus-details" className="station-focus-details">
      <section className="station-population" aria-busy={loading} aria-live="polite">
        <div className="station-section-head"><h3>周邊居住人口</h3><span className="station-badge">戶籍估算</span></div>
        {loading ? <p className="station-empty-title">人口資料載入中…</p>
          : failed ? <div className="station-empty"><b>人口資料載入失敗</b><p>設施資訊仍可查閱。</p><button className="station-text-button" onClick={() => { setFailed(false); setAttempt((a) => a + 1); }}>重新載入</button></div>
            : people === null ? <div className="station-empty"><b>尚無人口資料</b><p>此範圍尚無可用的臺北市戶籍人口資料。</p></div>
              : <><p className="station-population-value">{people.toLocaleString('zh-TW')}<span>人</span></p>
                <p className="station-meta">約 {households?.toLocaleString('zh-TW') ?? '—'} 戶 · {period} 戶籍人口</p>
                {share < 1 && <p className="station-warning">僅 {Math.round(share * 100)}% 範圍有臺北市資料，其餘未計。</p>}
              </>}
        <h3 className="station-age-title">年齡分布</h3>
        <div className="station-empty station-age-empty"><p>暫無可用的年齡分布</p><small>現有來源未提供年齡資料</small></div>
      </section>

      <section className="station-facilities">
        <div className="station-section-head"><h3>附近重要設施</h3><span>{STATION_RADIUS} m 內</span></div>
        {facilities.length ? <ul>
          {facilities.map((f) => <li key={f.id}><span className="station-facility-kind">{f.label}</span><span className="station-facility-name">{f.name}</span><span className="station-distance">約 {f.metres} m</span></li>)}
        </ul> : <p className="station-empty">範圍內沒有目前所選類別的設施。</p>}
        {surroundings.facilities.length > 3 && <button className="station-text-button" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>{expanded ? '收合設施' : `查看全部 ${surroundings.facilities.length} 處設施`}</button>}
      </section>

      <details className="station-more" key={`${station.type}:${station.id}`}>
        <summary>站點與周邊交通</summary>
        {station.type === 'bus' && <><p>{station.properties.route_count} 條路線 · 平日計畫發車 {station.properties.weekday_departures ?? '—'} 班</p><p className="station-routes">{station.properties.routes.join('、') || '尚無路線資料'}</p></>}
        {station.type === 'mrt' && <p>以車站點位估算，並非各出入口的步行範圍。</p>}
        {station.type === 'youbike' && <><p>{station.properties.district ?? '行政區未提供'} · {station.properties.dock_count ?? '—'} 個停車格</p><p>{station.properties.is_active === false ? '資料時間點暫停營運' : station.properties.is_active === true ? '資料時間點營運中' : '營運狀態未提供'}；無即時可借車數。</p></>}
        <p>{station.type === 'bus' ? '其他公車站位' : '公車站位'} {surroundings.stops} 個 · 捷運 {surroundings.mrt.count} 站 · YouBike {surroundings.youbike.count} 站</p>
        {station.type !== 'mrt' && surroundings.mrt.nearest && <p>最近捷運：{surroundings.mrt.nearest.name} · 約 {surroundings.mrt.nearest.metres} m</p>}
        {surroundings.youbike.count > 0 && <p>範圍內 YouBike 停車格：{surroundings.youbike.docks?.toLocaleString('zh-TW') ?? '資料不完整'}；無即時可借車數。</p>}
        <p className="muted">同類站點統計含本站（公車站位除外）。{station.type === 'bus' && '公車計畫班次不代表本站到站時刻。'}</p>
        <p className="muted">人口依統計區面積分攤，相鄰範圍不可加總。{catchments && <>人口 {period}、邊界 {catchments.metadata.boundary_vintage} 年。</>}設施依地點篩選，為未去重的候選位置，非出入口。</p>
      </details>

      <footer className="station-note">戶籍人口估算，非搭乘量 · 設施距離採直線估算</footer>
      </div>
    </section>
  );
}
