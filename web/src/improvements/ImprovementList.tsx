import { forwardRef, useDeferredValue, useEffect, useImperativeHandle, useMemo, useRef, useState, type ReactNode } from 'react';
import { DAY_LABELS } from '../config';
import type { MapData } from '../types';
import Dialog from '../routeEditor/Dialog';
import Detail, { Mock, number, Score, Tags } from './Detail';
import ImprovementMap from './ImprovementMap';
import { csv, loadFilters, matches, rankEntries, saveFilters, sortRows } from './model';
import { DEFAULTS, KINDS, PERIODS, PROBLEMS, type Entry, type Filters, type ImprovementData, type Kind, type Ranked, type SortKey, type Weights } from './types';
import './improvements.css';

export interface ImprovementHandle { exportCsv: () => void; preferences: () => void }
interface Props { map: MapData | null; onEdit: (variant: string, filters: Filters) => void; onTime: (filters: Filters) => void }
interface Column { key: SortKey; title: string; render: (r: Ranked) => ReactNode; className?: string }
const PAGE_SIZE = 10;
const frequency = (r: Ranked, n = r.frequency) => `${number(n)}${r.partial && n !== null ? '†' : ''}`;

function Meter({ value, color = '#8c6bc7' }: { value: number | null; color?: string }) {
  return <span className="im-meter"><b>{number(value, 0)}{value === null ? '' : '%'}</b><i><span style={{ width: `${value ?? 0}%`, background: color }} /></i></span>;
}
const common: Record<string, Column> = {
  name: { key: 'name', title: '路線', className: 'im-name-cell', render: (r) => <><button className="im-row-open" aria-label={`檢視${r.name}詳情`}>{r.name}</button><small>{r.kind === 'routes' ? `${r.stop_ids.length} 站 · ${frequency(r)} 班/h` : r.districts.join('・') || '區界未涵蓋'}</small></> },
  score: { key: 'score', title: '優先分數*', className: 'im-score-cell', render: (r) => <Score score={r.score} /> },
  problems: { key: 'problems', title: '主要問題*', render: (r) => <Tags row={r} /> },
  shared: { key: 'shared', title: '公車共站', render: (r) => <><b>{r.shared_routes} 條</b><small>{r.kind === 'routes' ? '最多共站' : '主路線 ID 去重'}</small></> },
  passengers: { key: 'passengers', title: '每班次載客*', render: (r) => <><b className="im-load">{number(r.passengers)} 人</b><small>時段平均 · MOCK</small></> },
  cold: { key: 'cold', title: '冷區時段*', render: (r) => r.coldPeriod },
  recommendation: { key: 'recommendation', title: '建議處置*', className: 'im-recommend-cell', render: (r) => <>{r.recommendation}<span className="im-chevron">›</span></> },
  mrt: { key: 'mrt', title: '最近捷運站', render: (r) => <span className={r.nearest_mrt && r.nearest_mrt.distance_m <= 300 ? 'im-metro-text' : ''}>{r.nearest_mrt?.name ?? '—'}<small>{number(r.nearest_mrt?.distance_m, 0)} m・直線</small></span> },
  routes: { key: 'routes', title: '服務路線', render: (r) => `${r.route_ids.length} 條` },
};

function columns(kind: Kind): Column[] {
  const name = { ...common.name, title: kind === 'stops' ? '站位' : KINDS[kind] };
  if (kind === 'routes') return [name, common.score, common.problems,
    { key: 'geometry', title: '幾何', className: 'im-overlap-cell', render: (r) => <Meter value={r.geometry_overlap} /> },
    { key: 'station', title: '站點', className: 'im-overlap-cell', render: (r) => <Meter value={r.station_overlap} /> },
    { key: 'trip', title: '旅次*', className: 'im-overlap-cell', render: (r) => <Meter value={r.trip} color="#6e4ba4" /> },
    common.shared, common.passengers, common.cold, common.recommendation];
  if (kind === 'stops') return [name, common.score, { ...common.shared, title: '停靠路線數' },
    { key: 'frequency', title: '班次／小時 · 尖峰｜離峰', render: (r) => <span className="im-frequencies"><b>{frequency(r, r.peak)}</b><span>｜</span>{frequency(r, r.offpeak)}<small>已知計畫班表</small></span> },
    common.passengers, common.mrt, common.problems, common.recommendation];
  if (kind === 'drts') return [name, common.score,
    { key: 'population', title: '居住人口（推估）', render: (r) => <><b>{number(r.population, 0)}</b><small>2024-12{r.population_missing_cells ? '・部分缺資料' : ''}</small></> },
    common.routes, { key: 'frequency', title: '計畫班次／小時', render: (r) => frequency(r) }, common.passengers,
    { key: 'cold', title: '冷區比例*', render: (r) => <Meter value={r.coldShare * 100} color="#4b8ca0" /> }, common.problems, common.recommendation];
  return [name, common.score, common.routes,
    { key: 'uninterrupted', title: '連續行駛*', render: (r) => <b className={r.uninterrupted > 75 ? 'im-warning' : ''}>{r.uninterrupted} 分</b> },
    { key: 'rest', title: '端點休息*', render: (r) => `${r.rest} 分` },
    { key: 'toilet', title: '最近公廁', render: (r) => <>{r.nearest_toilet?.name ?? '資料未涵蓋'}<small>{r.nearest_toilet ? `${number(r.nearest_toilet.distance_m, 0)} m・直線` : '臺北市公廁資料'}</small></> },
    common.problems, common.recommendation];
}

function Stats({ rows, total, filters }: { rows: Ranked[]; total: number; filters: Filters }) {
  const sum = (get: (r: Ranked) => number) => rows.reduce((a, b) => a + get(b), 0);
  const count = (test: (r: Ranked) => boolean) => rows.filter(test).length;
  const stats = filters.kind === 'stops' ? [
    ['停靠 ≥30 條路線的站位', count((r) => r.shared_routes >= 30), '個・主路線去重', false],
    ['捷運 300 m 內的高堆疊站位', count((r) => r.station_overlap === 100 && r.shared_routes >= 15), '個・建議轉乘導向', false],
    [`每班次 <${filters.passengers} 人的站位`, count((r) => r.passengers < filters.passengers), '個・時段載客示意', true],
    ['可整併停靠（估算）', sum((r) => r.problems.includes('stack') ? Math.max(0, Math.round(r.shared_routes * .15)) : 0), '路線・站次', true],
  ] : filters.kind === 'drts' ? [
    ['高優先候選區塊（≥80 分）', count((r) => r.score >= 80), `／ ${total} 個地理區塊`, true],
    ['區塊居住人口（推估）', sum((r) => r.population ?? 0), '人・2024-12 已知小計', false],
    [`每班次 <${filters.passengers} 人的區塊`, count((r) => r.passengers < filters.passengers), '個・需求情境示意', true],
    ['可重新配置車輛時（估算）', sum((r) => r.problems.includes('cold') ? r.releasedHours : 0), '車·時／週', true],
  ] : filters.kind === 'terminals' ? [
    ['站序端點', rows.length, '個・非已驗證調度站', false],
    ['300 m 內有公廁', count((r) => !!r.nearest_toilet && r.nearest_toilet.distance_m <= 300), '個・僅臺北市公廁', false],
    ['連續行駛 >75 分鐘', count((r) => r.uninterrupted > 75), '個・勤務情境示意', true],
    ['休息少於 10 分鐘', count((r) => r.rest < 10), '個・勤務情境示意', true],
  ] : [
    ['高優先路線（≥80 分）', count((r) => r.score >= 80), `／ ${total} 條路線`, true],
    ['捷運旅次重疊 ≥40%', count((r) => r.trip >= 40), `條・站點重疊 ≥${filters.overlap}% 有 ${count((r) => r.station_overlap >= filters.overlap)} 條`, true],
    [`每班次 <${filters.passengers} 人`, count((r) => r.passengers < filters.passengers), '條・時段載客示意', true],
    ['可重新配置車輛時（估算）', sum((r) => r.problems.length ? r.releasedHours : 0), '車·時／週', true],
  ];
  return <div className="im-stats">{stats.map(([label, value, hint, mock]) => <section key={String(label)}><p>{label}{mock && <Mock short />}</p><strong>{number(value as number, 0)}</strong><span>{hint}</span></section>)}</div>;
}

const ImprovementList = forwardRef<ImprovementHandle, Props>(function ImprovementList({ map, onEdit, onTime }, ref) {
  const [data, setData] = useState<ImprovementData | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [filters, setFilters] = useState<Filters>(() => loadFilters(window.location.hash));
  const [page, setPage] = useState(1);
  const [dialog, setDialog] = useState<'weights' | 'methods' | null>(null);
  const [weights, setWeights] = useState<Weights>(filters.weights);
  const [message, setMessage] = useState('');
  const scroll = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const deferred = useDeferredValue(filters);
  const busy = filters !== deferred;
  const update = (patch: Partial<Filters>) => {
    setFilters((old) => ({ ...old, ...patch }));
    if (Object.keys(patch).some((key) => key !== 'view' && key !== 'selected')) { setPage(1); scroll.current?.scrollTo(0, 0); }
  };
  useEffect(() => { saveFilters(filters); onTime(filters); }, [filters, onTime]);
  useEffect(() => {
    const abort = new AbortController(); setError('');
    fetch(`${import.meta.env.BASE_URL}data/improvements.json`, { signal: abort.signal }).then((r) => { if (!r.ok) throw new Error(); return r.json(); })
      .then((d: ImprovementData) => { if (d.schema_version !== '1.0' || !Array.isArray(d.records)) throw new Error(); setData(d); })
      .catch((e: Error) => { if (e.name !== 'AbortError') setError('改善清單資料暫時無法載入。'); });
    return () => abort.abort();
  }, [retry]);
  useEffect(() => { if (!message) return; const timer = setTimeout(() => setMessage(''), 4500); return () => clearTimeout(timer); }, [message]);
  const all = useMemo(() => data?.records.filter((r) => r.kind === deferred.kind) ?? [], [data, deferred.kind]);
  const ranked = useMemo(() => rankEntries(all, deferred), [all, deferred]);
  const matched = useMemo(() => ranked.filter((r) => matches(r, deferred)), [ranked, deferred]);
  const rows = useMemo(() => sortRows(matched.filter((r) => deferred.showLow || r.score >= 60), deferred), [matched, deferred]);
  const lookup = useMemo(() => new Map<string, Entry>(data?.records.map((r) => [r.id, r])), [data]);
  const districts = useMemo(() => [...new Set(data?.records.flatMap((r) => r.districts) ?? [])].sort(), [data]);
  const selected = rows.find((r) => r.id === deferred.selected) ?? null;
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const visible = rows.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const cols = columns(filters.kind);
  const close = () => { update({ selected: null }); requestAnimationFrame(() => returnFocus.current?.isConnected && returnFocus.current.focus({ preventScroll: true })); };
  const select = (id: string, origin?: HTMLElement | null) => {
    if (id === filters.selected) { close(); return; }
    returnFocus.current = origin ?? document.activeElement as HTMLElement;
    setFilters((old) => ({ ...old, selected: id }));
  };
  useImperativeHandle(ref, () => ({ preferences: () => { setWeights(filters.weights); setDialog('weights'); }, exportCsv: () => {
    if (!data) { setMessage('資料仍在載入，請稍後再匯出。'); return; }
    if (busy) { setMessage('正在重算篩選結果，請稍後再匯出。'); return; }
    const link = document.createElement('a'); const url = URL.createObjectURL(new Blob([csv(rows, deferred, data.metadata.source_data_time)], { type: 'text/csv;charset=utf-8' }));
    link.href = url; link.download = `improvements-${filters.kind}-${filters.day}-${filters.period}.csv`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    setMessage(`已匯出 ${rows.length.toLocaleString()} 筆符合條件的資料，包含各欄 is_mock 標記。`);
  } }));
  const reset = () => update({ ...DEFAULTS, kind: filters.kind, view: filters.view });
  return <div className="improvement-page">
    <header className="im-heading"><div><h1>改善優先清單</h1><p>{KINDS[filters.kind]}｜{DAY_LABELS[filters.day]}・{PERIODS[filters.period].label}｜捷運重疊門檻 {filters.overlap}%・每班次門檻 {filters.passengers} 人｜{all.length.toLocaleString()} 筆中 {ranked.filter((r) => r.problems.length).length.toLocaleString()} 筆達到至少一項門檻</p></div>
      <div className="im-view-toggle" aria-label="清單呈現方式"><button aria-pressed={filters.view === 'map'} onClick={() => update({ view: 'map' })}>◫ 地圖視角</button><button aria-pressed={filters.view === 'list'} onClick={() => update({ view: 'list' })}>☰ 清單視角</button></div>
    </header>
    <nav className="im-categories" aria-label="改善對象">{(Object.keys(KINDS) as Kind[]).map((kind) => <button key={kind} aria-current={filters.kind === kind ? 'page' : undefined} onClick={() => update({ kind, selected: null, sort: 'score', descending: true })}>{KINDS[kind]} <span>{data?.metadata.counts[kind].toLocaleString() ?? '…'}</span></button>)}</nav>
    <Stats rows={matched} total={all.length} filters={deferred} />
    <div className="im-filters">
      <label>時段<select aria-label="日期類型" value={filters.day} onChange={(e) => update({ day: e.target.value as Filters['day'] })}>{Object.entries(DAY_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <select aria-label="分析時段" value={filters.period} onChange={(e) => update({ period: e.target.value as Filters['period'] })}>{Object.entries(PERIODS).map(([key, band]) => <option key={key} value={key}>{band.label}</option>)}</select>
      <label className="im-district">行政區<select aria-label="行政區" value={filters.district} onChange={(e) => update({ district: e.target.value })}><option value="">全部</option>{districts.map((d) => <option key={d}>{d}</option>)}</select></label>
      <div className="im-problem-filters" role="group" aria-label="問題篩選"><span>問題</span><button aria-pressed={!filters.problems.length} onClick={() => update({ problems: [] })}>全部</button>{Object.entries(PROBLEMS).map(([key, label]) => <button className={`im-${key}`} key={key} aria-pressed={filters.problems.includes(key as keyof typeof PROBLEMS)} onClick={() => update({ problems: filters.problems.includes(key as keyof typeof PROBLEMS) ? filters.problems.filter((p) => p !== key) : [...filters.problems, key as keyof typeof PROBLEMS] })}><i />{label}</button>)}</div>
      <label className="im-slider">重疊門檻<input aria-label="捷運站點重疊門檻" type="range" min="10" max="100" step="5" value={filters.overlap} onChange={(e) => update({ overlap: +e.target.value })} /><b>{filters.overlap}%</b></label>
      <label className="im-slider">每班次<input aria-label="每班次載客門檻" type="range" min="1" max="30" value={filters.passengers} onChange={(e) => update({ passengers: +e.target.value })} /><b>{filters.passengers} 人</b></label>
      <input className="im-search" type="search" aria-label="搜尋改善清單" placeholder={filters.kind === 'routes' ? '⌕ 搜尋路線名稱或編號' : '⌕ 搜尋站位、區塊或地點'} value={filters.query} onChange={(e) => update({ query: e.target.value })} />
    </div>
    <div className="im-results" aria-busy={busy}>
      {busy && <div className="im-computing" role="status">正在重算分數與排序…</div>}
      {!data ? <div className="im-empty" role={error ? 'alert' : 'status'}><h2>{error || '正在載入改善清單…'}</h2><p>準備路線、站位、班表與空間重疊指標</p>{error && <button className="im-primary" onClick={() => setRetry((n) => n + 1)}>重新載入</button>}</div>
      : !rows.length ? <div className="im-empty"><span>⌕</span><h2>沒有符合目前門檻的{KINDS[filters.kind]}</h2><p>試著放寬篩選條件，或展開低優先項目。</p><div><button className="im-primary" onClick={reset}>重設為預設值</button>{!filters.showLow && <button className="btn-outline" onClick={() => update({ showLow: true })}>顯示低優先項目</button>}</div></div>
      : filters.view === 'map' ? (map ? <ImprovementMap rows={rows} selected={selected} map={map} onSelect={select} /> : <div className="im-empty" role="status">正在載入地圖…</div>)
      : <div className="im-table-scroll" ref={scroll}><table className={`im-table im-table-${filters.kind}`}><thead><tr><th className="im-rank-column">#</th>{cols.map((col) => <th key={col.key} className={col.className} aria-sort={filters.sort === col.key ? (filters.descending ? 'descending' : 'ascending') : 'none'}><button onClick={() => update({ sort: col.key, descending: filters.sort === col.key ? !filters.descending : col.key !== 'name' })}>{col.title}{filters.sort === col.key && <span>{filters.descending ? ' ↓' : ' ↑'}</span>}</button></th>)}</tr></thead>
        <tbody>{visible.map((row, i) => <tr key={row.id} className={selected?.id === row.id ? 'is-selected' : ''} onClick={(e) => select(row.id, e.currentTarget.querySelector('button'))} aria-selected={selected?.id === row.id}><td className="im-rank-column">{(currentPage - 1) * PAGE_SIZE + i + 1}</td>{cols.map((col) => <td key={col.key} className={col.className}>{col.render(row)}</td>)}</tr>)}</tbody>
      </table></div>}
      <footer className="im-table-footer"><span>{filters.view === 'list' ? `顯示 ${rows.length ? (currentPage - 1) * PAGE_SIZE + 1 : 0}–${Math.min(currentPage * PAGE_SIZE, rows.length)}` : '地圖顯示'}，共 {rows.length.toLocaleString()} 筆</span>
        <label><input type="checkbox" checked={filters.showLow} onChange={(e) => update({ showLow: e.target.checked })} />含低優先（&lt;60 分）</label>
        <button className="im-method-link" onClick={() => setDialog('methods')}>＊需求、分數與建議含 Mock Data；† 班表不完整。指標與來源 ↗</button>
        {filters.view === 'list' && <nav className="im-pagination" aria-label="清單分頁"><button aria-label="上一頁" disabled={currentPage === 1} onClick={() => { setPage(currentPage - 1); scroll.current?.scrollTo(0, 0); }}>‹</button>
          {[...new Set([1, currentPage - 1, currentPage, currentPage + 1, totalPages])].filter((n) => n >= 1 && n <= totalPages).sort((a, b) => a - b).map((n, i, ns) => <span key={n}>{i > 0 && n - ns[i - 1] > 1 && <i>…</i>}<button aria-current={currentPage === n ? 'page' : undefined} onClick={() => { setPage(n); scroll.current?.scrollTo(0, 0); }}>{n}</button></span>)}
          <button aria-label="下一頁" disabled={currentPage === totalPages} onClick={() => { setPage(currentPage + 1); scroll.current?.scrollTo(0, 0); }}>›</button></nav>}
      </footer>
    </div>
    {selected && <Detail row={selected} rank={rows.indexOf(selected) + 1} total={rows.length} filters={deferred} lookup={lookup} onClose={close}
      onMap={() => update({ view: 'map' })} onEdit={(id) => onEdit(id, filters)} onMethods={() => setDialog('methods')} />}
    {dialog === 'weights' && <Dialog title="改善清單偏好" onClose={() => setDialog(null)}><p>調整四項指標權重，總分會依比例換算為 100 分。需求與旅次使用 Mock Data。</p>
      <div className="im-weight-fields">{['捷運重疊', '路線堆疊', '低載程度', '冷區比例'].map((label, i) => <label key={label}>{label}<input type="range" min="0" max="100" value={weights[i]} onChange={(e) => setWeights(weights.map((w, j) => i === j ? +e.target.value : w) as Weights)} /><b>{weights[i]}</b></label>)}</div>
      <footer><button className="btn-outline" onClick={() => setWeights(DEFAULTS.weights)}>恢復預設權重</button><button className="im-primary" disabled={!weights.some((n) => n > 0)} onClick={() => { update({ weights }); setDialog(null); }}>套用</button></footer></Dialog>}
    {dialog === 'methods' && <Dialog title="改善清單・指標定義與資料來源" onClose={() => setDialog(null)}><div className="im-methods">
      <p><Mock /> 分數、問題標籤、載客量、旅次重疊、冷區、勤務休息與建議影響含固定種子的模擬資料。切換時段可展示互動，不代表營運實測。</p>
      <h3>優先分數與門檻</h3><p>捷運 {filters.weights[0]}、堆疊 {filters.weights[1]}、低載 {filters.weights[2]}、冷區 {filters.weights[3]}，按權重總和正規化至 100 分。高優先 ≥80，中優先 60–79，低於 60 預設收合。</p>
      <p>捷運項以旅次重疊相對 40% 的比例（75%）與站點相對目前門檻的比例（25%）計分，各自上限 1。堆疊取共站路線／30 與班次／同類別前 10% 班次門檻的較大值，上限 1。低載取 1−載客量／兩倍人數門檻，最低 0；冷區採 Mock 比例。</p>
      <p>標籤：旅次 ≥40% 或站點 ≥{filters.overlap}%；共站 ≥15 條或班次在同類前 10%；載客 &lt;{filters.passengers} 人或 Mock 冷區占比 ≥50%；Mock 連續行駛 &gt;75 分鐘。多個問題篩選採任一符合。</p>
      <h3>真實供給與空間資料</h3><p>公運處原生主路線、附線、站序與站位 ID。班次採週三、週六、週日代表班表，附線去重且不按方向重複加總；以起站時刻計算，不是到站預估。全日時段 00–24 時；尖峰 07–09、17–19，離峰 09–17。† 表示只有已知班表小計，完全缺少則為「—」。</p>
      <p>幾何重疊：可用原始線形聯集在 OSM 捷運線 100 m 緩衝內的長度比例；站點重疊：不重複實體站位到 OSM 站心 300 m 內的比例。距離以 EPSG:3826 計算，非步行路網。公廁僅臺北市，無營業時間或停車保證。</p>
      <p>DRTS 以 1 km TM2 地理方格呈現，人口來自 2024-12 統計與 2015 統計區界之面積分攤，按既有 100 m 格心彙整；候選區不是 H3/Gi* 的實測冷點。端點是站序首末站，不等同核實的調度場站。</p>
      <p>公車快照：{data?.metadata.source_data_time ?? '載入中'}。僅臺北市主管機關路線，保留跨市服務；新北市專屬路線未涵蓋。</p>
      <p><a href="https://pto.gov.taipei/News_Content.aspx?n=A1DF07A86105B6BB&s=55E8ADD164E4F579" target="_blank" rel="noreferrer">公運處來源與 API 規格 ↗</a> · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OSM 圖資 ↗</a> · <a href="https://data.taipei/dataset/detail?id=ca205b54-a06f-4d84-894c-d6ab5079ce79" target="_blank" rel="noreferrer">臺北市公廁 ↗</a></p>
    </div><footer><button className="im-primary" onClick={() => setDialog(null)}>了解</button></footer></Dialog>}
    {message && <div className="im-toast" role="status">{message}</div>}
  </div>;
});
export default ImprovementList;
