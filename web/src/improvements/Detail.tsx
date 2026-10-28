import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { DAY_LABELS } from '../config';
import { sample } from './model';
import { KINDS, PERIODS, PROBLEMS, type Entry, type Filters, type Ranked } from './types';

export const number = (v: number | null | undefined, digits = 1) => v === null || v === undefined ? '—' : v.toLocaleString('zh-TW', { maximumFractionDigits: digits });
export function Mock({ short = false }: { short?: boolean }) { return <span className="im-mock">{short ? 'MOCK' : 'Mock Data'}</span>; }
export function Tags({ row }: { row: Ranked }) { return <div className="im-tags">{row.problems.length ? row.problems.map((p) => <span key={p} className={`im-tag im-${p}`}>{PROBLEMS[p]}</span>) : <span className="muted">待觀察</span>}</div>; }
export function Score({ score }: { score: number }) { return <div className={`im-score ${score >= 80 ? 'is-high' : score >= 60 ? 'is-medium' : 'is-low'}`}>
  <strong>{score}</strong><span>{score >= 80 ? '高' : score >= 60 ? '中' : '低'}</span><i><b style={{ width: `${score}%` }} /></i>
</div>; }
function Metric({ label, value, hint }: { label: string; value: string; hint: string }) {
  return <div className="im-detail-metric"><span>{label}</span><strong>{value}</strong><small>{hint}</small></div>;
}
function Profile({ row, filters, lookup }: { row: Ranked; filters: Filters; lookup: Map<string, Entry> }) {
  const sids = row.profile_stops ?? row.stop_ids;
  const values = sids.map((sid, i) => ({ sid, name: lookup.get(`stop:${sid}`)?.name ?? sid,
    near: lookup.get(`stop:${sid}`)?.station_overlap === 100,
    value: Number((row.passengers * (1 + 1.5 * Math.sin(Math.PI * i / Math.max(1, sids.length - 1))) * (.7 + sample(`${row.id}:${filters.day}:${filters.period}:${i}`) * .6)).toFixed(1)),
  }));
  const max = Math.max(1, Math.ceil(Math.max(...values.map((v) => v.value))));
  return <section className="im-detail-section"><h3>載客剖面 · {DAY_LABELS[filters.day]} · {PERIODS[filters.period].label}<Mock short /></h3>
    <p className="im-small">{row.profile_direction} · 沿用真實站序；紫色為 300 m 內有捷運的站位</p>
    <svg className="im-profile" viewBox="0 0 460 164" role="img" aria-label="模擬載客剖面，車上人數為 Mock Data">
      <line x1="18" y1="140" x2="455" y2="140" stroke="#dce6e7" />
      <text x="0" y="13">{max} 人</text>
      {values.map((v, i) => <rect key={`${v.sid}:${i}`} x={20 + i * 432 / values.length} y={140 - 116 * v.value / max}
        width={Math.max(1, 432 / values.length - 2)} height={116 * v.value / max} rx="1.5" fill={v.near ? '#8d6ac8' : '#7cc3bd'}>
        <title>{i + 1}. {v.name}：{v.value} 人（Mock Data）</title></rect>)}
      <text x="20" y="159">{values[0]?.name}</text><text x="455" y="159" textAnchor="end">{values.at(-1)?.name}</text>
    </svg>
  </section>;
}
function Schedule({ row, filters }: { row: Ranked; filters: Filters }) {
  const hours = row.hourly[filters.day];
  const max = Math.max(1, ...(hours ?? []));
  return <section className="im-detail-section"><h3>每小時計畫發車 <span className="im-real">真實班表</span></h3>
    {hours ? <svg className="im-profile" viewBox="0 0 460 150" role="img" aria-label={`${DAY_LABELS[filters.day]}每小時計畫班次`}>
      {hours.map((v, i) => <g key={i}><rect x={10 + i * 18.5} y={120 - v / max * 105} width="13" height={v / max * 105} rx="2" fill={i >= PERIODS[filters.period].start && i < PERIODS[filters.period].end ? '#337cda' : '#cbdcec'}><title>{i} 時：{number(v)} 班</title></rect>
        {i % 3 === 0 && <text x={10 + i * 18.5} y="140">{i}時</text>}</g>)}
    </svg> : <p className="im-empty-inline">此時段缺少班表資料。</p>}
    <p className="im-small">依起站發車小時計入，不是本站到站時刻。{row.partial && `有 ${row.missing_schedules[filters.day]} 條附線缺少當日班表，顯示已知小計。`}</p>
  </section>;
}

interface Props { row: Ranked; rank: number; total: number; filters: Filters; lookup: Map<string, Entry>;
  onClose: () => void; onMap: () => void; onEdit: (id: string) => void; onMethods: () => void }
export default function Detail({ row, rank, total, filters, lookup, onClose, onMap, onEdit, onMethods }: Props) {
  const panel = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const [routeId, setRouteId] = useState(row.route_ids[0] ?? '');
  useLayoutEffect(() => {
    const compact = window.matchMedia('(max-width: 900px)');
    const previous = new Map<HTMLElement, boolean>();
    const restore = () => { previous.forEach((inert, element) => { element.inert = inert; }); previous.clear(); };
    const sync = () => {
      restore();
      if (!compact.matches || !panel.current?.parentElement) return;
      // The compact detail covers this page; the global navigation remains available.
      const covered = [...panel.current.parentElement.children].filter((element): element is HTMLElement =>
        element instanceof HTMLElement && element !== panel.current && element.tagName !== 'DIALOG' && element.getAttribute('role') !== 'status');
      const focusCovered = covered.some((element) => element.contains(document.activeElement));
      covered.forEach((element) => { previous.set(element, element.inert); element.inert = true; });
      if (focusCovered) heading.current?.focus({ preventScroll: true });
    };
    sync(); compact.addEventListener('change', sync);
    return () => { compact.removeEventListener('change', sync); restore(); };
  }, []);
  useEffect(() => { setRouteId(row.route_ids[0] ?? ''); heading.current?.focus({ preventScroll: true }); }, [row.id, row.route_ids]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape' && !document.querySelector('dialog[open]')) onClose(); };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [onClose]);
  const weighted = filters.weights.map((w) => w * 100 / filters.weights.reduce((a, b) => a + b, 0));
  const target = row.kind === 'routes' ? row.variant_id : lookup.get(`route:${routeId}`)?.variant_id;
  return <aside ref={panel} className="im-detail" aria-label={`${row.name}詳情`}>
    <div className="im-detail-scroll">
      <header><p className="im-small">{KINDS[row.kind]}詳情 · 優先排名 #{rank}</p><button className="icon-btn" aria-label="關閉詳情" onClick={onClose}>✕</button>
        <h2 ref={heading} tabIndex={-1}>{row.name}</h2>
        <p className="im-small">{row.stop_ids.length} 站 · {DAY_LABELS[filters.day]} {number(row.frequency)} 班/h{row.partial ? '（已知小計）' : ''}{row.subtitle ? ` · ${row.subtitle}` : ''}</p>
        <Tags row={row} />
      </header>
      <section className="im-score-card"><div><span>優先分數 <Mock short /></span><div className="im-score-number">{row.score}<small>/ 100</small></div>
        <p>{total.toLocaleString()} 筆中第 {rank}<br />前 {Math.max(1, Math.ceil(rank / total * 100))}%</p></div>
        <div className="im-breakdown">{['捷運重疊', '路線堆疊', '低載程度', '冷區比例'].map((label, i) => <div key={label}><span>{label}</span><i><b style={{ width: `${weighted[i] ? row.components[i] / weighted[i] * 100 : 0}%` }} /></i><strong>{number(row.components[i])}/{number(weighted[i])}</strong></div>)}</div>
      </section>
      {row.kind === 'routes' && <>
        <section className="im-detail-section"><h3>三種捷運重疊度 <Mock /></h3><div className="im-metric-grid">
          <Metric label="幾何重疊 · 真實" value={`${number(row.geometry_overlap)}${row.geometry_overlap === null ? '' : '%'}`} hint="線形 100 m 緩衝內" />
          <Metric label="站點重疊 · 真實" value={`${number(row.station_overlap)}%`} hint="站位 300 m 內有捷運" />
          <Metric label="旅次重疊 · MOCK" value={`${number(row.trip)}%`} hint="可替代旅次的示意值" />
        </div><p className="im-small">{row.geometry_directions}/{row.total_directions} 個方向具可比對線形；站點以 OSM 站心計算。旅次缺少票證 OD，以 Mock Data 示意，不能據此判定實際裁撤效果。</p></section>
        <Profile row={row} filters={filters} lookup={lookup} />
      </>}
      {row.kind === 'stops' && <section className="im-detail-section"><h3>站位與轉乘 <span className="im-real">真實資料</span></h3><div className="im-metric-grid">
        <Metric label="停靠路線" value={String(row.route_ids.length)} hint="主路線 ID 去重" />
        <Metric label="最近捷運" value={row.nearest_mrt?.name ?? '—'} hint={`${number(row.nearest_mrt?.distance_m, 0)} m・直線距離`} />
        <Metric label="離峰每班次 · MOCK" value={`${number(row.passengers)} 人`} hint="載客量為示意值" />
      </div><p className="im-small">目前清單以實體站位呈現；共線與路段調整可在路線工具檢視。站牌不依同名合併。</p></section>}
      {row.kind === 'drts' && <section className="im-detail-section"><h3>區塊需求與服務 <Mock /></h3><div className="im-metric-grid">
        <Metric label="居住人口 · 推估" value={number(row.population, 0)} hint="2024-12・面積分攤" />
        <Metric label="區內站位 · 真實" value={String(row.stop_ids.length)} hint={`${row.route_ids.length} 條主路線`} />
        <Metric label="冷區比例 · MOCK" value={`${Math.round(row.coldShare * 100)}%`} hint="示意值，非 Gi* 統計" />
      </div><p className="im-small">以 1 km 方格瀏覽服務範圍；候選資格與預約需求為 Mock Data。人口由既有 100 m 網格按格心彙整，{row.population_missing_cells ? `${row.population_missing_cells} 格缺少人口資料，僅顯示已知小計` : '統計區界版本為 2015 年'}。</p></section>}
      {row.kind === 'terminals' && <section className="im-detail-section"><h3>休息與如廁條件 <Mock /></h3><div className="im-metric-grid">
        <Metric label="連續行駛 · MOCK" value={`${row.uninterrupted} 分`} hint="班表情境示意" />
        <Metric label="端點休息 · MOCK" value={`${row.rest} 分`} hint="非實際勤務紀錄" />
        <Metric label="最近公廁 · 真實" value={row.nearest_toilet ? `${number(row.nearest_toilet.distance_m, 0)} m` : '—'} hint={row.nearest_toilet?.name ?? '臺北市外資料未涵蓋'} />
      </div><p className="im-small">端點取自 {row.terminal_directions} 個附線方向的首末站，不等同調度站。公廁距離不代表可停車、可進入或可用休息時間。</p></section>}
      {row.kind !== 'routes' && <Schedule row={row} filters={filters} />}
      <section className="im-recommendation"><h3>建議處置 <Mock short /></h3><strong>{row.recommendation}</strong>
        <p>規則依目前門檻產生，先在路線工具試算調整；保留非重疊服務與必要轉乘。</p><div className="im-impact-values">
          <Metric label="350 m 可達人口" value={`${number(row.populationChange)}%`} hint="Mock Data" />
          <Metric label="受影響旅次／日" value={number(row.affectedTrips, 0)} hint="Mock Data" />
          <Metric label="釋放車·時／週" value={number(row.releasedHours, 0)} hint="Mock Data" />
        </div>
      </section>
      {row.kind !== 'routes' && <label className="im-route-picker">選擇要編輯的路線<select value={routeId} onChange={(e) => setRouteId(e.target.value)}>{row.route_ids.map((id) => <option key={id} value={id}>{lookup.get(`route:${id}`)?.name ?? id}</option>)}</select></label>}
    </div>
    <footer><div><button className="btn-outline" onClick={onMap}>◫ 在地圖上檢視</button><button className="im-primary" disabled={!target} onClick={() => target && onEdit(target)}>加入路線編輯方案 →</button></div>
      <button className="im-method-link" onClick={onMethods}>分數、需求與影響含 Mock Data；查看指標定義與來源。</button>
    </footer>
  </aside>;
}
