import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import type { MapRef } from 'react-map-gl/maplibre';
import type { FeatureCollection, Polygon } from 'geojson';
import MapView from '../map/MapView';
import Search from '../components/Search';
import type { AppState, Grid, MapData, StationSelection, ViewState } from '../types';
import type { Filters } from '../improvements/types';
import { draftChanges, frequency, gridFeatures, routePath, routesWithChanges } from './engine';
import { buildExistingServices } from './existingServices';
import { loadScenario, saveScenario } from './storage';
import { searchRouteGroups } from './search';
import Dialog from './Dialog';
import ExistingServicesPanel from './ExistingServicesPanel';
import Impact, { Duration, PopulationSummary, rate } from './Impact';
import { ACTION_LABELS, BANDS, type AppliedAction, type Assessment, type Band, type Changes, type Draft, type Route, type ScenarioData } from './types';
import type { EditorMapProps } from './RouteLayers';
import './routeEditor.css';

export interface RouteWorkspaceHandle { leave: (done: () => void) => void; preferences: () => void; indicators: () => void }
export interface RouteTarget { variant: string; day: Filters['day']; period: Filters['period'] }
interface Props { map: MapData; grid: Grid; gridData: FeatureCollection<Polygon, { k: number }>; state: AppState; initialView: ViewState; target?: RouteTarget | null }

const RouteWorkspace = forwardRef<RouteWorkspaceHandle, Props>(function RouteWorkspace(props, ref) {
  const [data, setData] = useState<ScenarioData | null>(null);
  const [loadError, setLoadError] = useState('');
  const [retry, setRetry] = useState(0);
  const innerRef = useRef<RouteWorkspaceHandle>(null);
  useImperativeHandle(ref, () => ({ leave: (done) => innerRef.current ? innerRef.current.leave(done) : done(),
    preferences: () => innerRef.current?.preferences(), indicators: () => innerRef.current?.indicators() }), []);
  useEffect(() => {
    const controller = new AbortController();
    setLoadError('');
    fetch(`${import.meta.env.BASE_URL}data/route_scenario.json`, { signal: controller.signal })
      .then((r) => { if (!r.ok) throw new Error(); return r.json(); })
      .then((d: ScenarioData) => { if (d.schema_version !== '1.0' || !d.records?.routes.length) throw new Error(); setData(d); })
      .catch((error: Error) => { if (error.name !== 'AbortError') setLoadError('路線資料暫時無法載入，請重試。'); });
    return () => controller.abort();
  }, [retry]);
  return data ? <Workspace ref={innerRef} {...props} data={data} />
    : <div className="card route-loading" role={loadError ? 'alert' : 'status'}><h2>{loadError || '正在載入路線工具…'}</h2>
      <p>準備現況站序、班表與人口網格</p>{loadError && <button className="route-primary" onClick={() => setRetry((n) => n + 1)}>重新載入</button>}</div>;
});
export default RouteWorkspace;

const Workspace = forwardRef<RouteWorkspaceHandle, Props & { data: ScenarioData }>(function Workspace({ data, map, grid, gridData, state, initialView, target }, ref) {
  const mapRef = useRef<MapRef>(null);
  const [saved] = useState(() => loadScenario(data));
  const [history, setHistory] = useState<AppliedAction[]>(saved.history);
  const [changes, setChanges] = useState<Changes>(saved.changes);
  const [message, setMessage] = useState(saved.message);
  const [band, setBand] = useState<Band>(() => target ? target.day === 'weekday' && target.period !== 'all' ? target.period : `${target.day}_${target.period}` as Band : history.at(-1)?.band ?? 'am');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState(() => target?.variant ?? data.records.routes.find((r) => r.name === '仁愛幹線' && r.direction === '1')?.id ?? data.records.routes[0].id);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [step, setStep] = useState<'overview' | 'edit' | 'preview' | 'review'>('overview');
  const [interaction, setInteraction] = useState<'stops' | 'path'>('stops');
  const [edits, setEdits] = useState<Route[]>([]);
  const [editIndex, setEditIndex] = useState(0);
  const [stopQuery, setStopQuery] = useState('');
  const [insertAfter, setInsertAfter] = useState(-1);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dialog, setDialog] = useState<'discard' | 'metrics' | 'preferences' | null>(null);
  const exit = useRef<(() => void) | null>(null);
  const [station, setStation] = useState<StationSelection | null>(null);
  const [desktop, setDesktop] = useState(() => window.matchMedia('(min-width: 901px)').matches);
  const [highlightedServiceRouteId, setHighlightedServiceRouteId] = useState<string | null>(null);
  const [settings, setSettings] = useState({ gridLines: true, bike: true, labels: state.labels, speed: 18, dwell: 30 });
  const [prefDraft, setPrefDraft] = useState(settings);
  const [retry, setRetry] = useState(0);
  const stageRef = useRef<HTMLDivElement>(null);
  const routes = useMemo(() => routesWithChanges(data, changes), [data, changes]);
  const selected = routes.find((r) => r.id === selectedId) ?? routes[0] ?? null;
  const last = history.at(-1);
  const activeDraft = step === 'review' && last ? { kind: last.kind, route: last.route, original: last.original, band: last.band } : draft;
  const effectiveBand = activeDraft?.band ?? band;
  const selectedStopIds = draft?.route.stop_ids;
  const showExistingServices = desktop && step === 'edit' && draft?.kind === 'add';
  const existingServices = useMemo(() => showExistingServices && selectedStopIds
    ? buildExistingServices(data, routes, selectedStopIds, effectiveBand) : null,
  [data, routes, selectedStopIds, effectiveBand, showExistingServices]);
  const activeServiceRouteId = existingServices?.some((service) => service.route.route_id === highlightedServiceRouteId) ? highlightedServiceRouteId : null;
  const evaluated = useMemo(() => draft ? draftChanges(data, changes, draft) : changes, [data, changes, draft]);
  const request = useMemo(() => ({ changes: evaluated, band: effectiveBand, retry }), [evaluated, effectiveBand, retry]);
  const [response, setResponse] = useState<{ request: typeof request; result?: Assessment; error?: string } | null>(null);
  // Match during render, rather than waiting for an effect to invalidate the previous result.
  const busy = response?.request !== request;
  const result = !busy ? response?.result ?? null : null;
  const error = !busy ? response?.error ?? '' : '';
  const dirty = draft !== null && JSON.stringify(draft.route) !== JSON.stringify(draft.kind === 'add' ? edits[0] : draft.original);

  useEffect(() => {
    const query = window.matchMedia('(min-width: 901px)');
    const update = () => setDesktop(query.matches);
    update(); query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    if (highlightedServiceRouteId !== activeServiceRouteId) setHighlightedServiceRouteId(null);
  }, [activeServiceRouteId, highlightedServiceRouteId]);
  useEffect(() => {
    const worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = (event: MessageEvent<{ result?: Assessment; error?: string }>) => {
      setResponse({ request, ...event.data });
    };
    worker.onerror = () => setResponse({ request, error: '試算暫時無法執行，請重試。草稿仍保留。' });
    const timer = setTimeout(() => worker.postMessage({ id: 1, data, map, changes: request.changes, band: request.band }), 100);
    return () => { clearTimeout(timer); worker.terminate(); };
  }, [data, map, request]);
  const raster = useMemo(() => response?.result ? gridFeatures(data, response.result) : null, [data, response]);

  const clearDraft = () => { setDraft(null); setStep('overview'); setStation(null); setInteraction('stops'); setStopQuery(''); };
  const leave = (done: () => void) => {
    if (dirty) { exit.current = done; setDialog('discard'); }
    else { clearDraft(); done(); }
  };
  useImperativeHandle(ref, () => ({ leave, preferences: () => { setPrefDraft(settings); setDialog('preferences'); }, indicators: () => setDialog('metrics') }));
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [dirty]);
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setMessage(''), 6000);
    return () => clearTimeout(timer);
  }, [message]);
  useEffect(() => {
    stageRef.current?.querySelector<HTMLElement>('h1, .route-impact h2')?.focus({ preventScroll: true });
  }, [step]);
  useEffect(() => {
    const list = stageRef.current?.querySelector<HTMLElement>('.route-list');
    const item = list?.querySelector<HTMLElement>('.is-selected');
    if (list && item) list.scrollTop = item.offsetTop - list.offsetTop - list.clientHeight / 2 + item.clientHeight / 2;
  }, [selectedId, step]);

  const fitRoute = useCallback((r: Route) => {
    const pts = routePath(r, data);
    if (!pts.length) return;
    const lng = pts.map((p) => p[0]), lat = pts.map((p) => p[1]);
    const narrow = window.innerWidth < 900;
    mapRef.current?.fitBounds([[Math.min(...lng), Math.min(...lat)], [Math.max(...lng), Math.max(...lat)]], {
      padding: narrow ? { top: 90, bottom: window.innerHeight * .5, left: 30, right: 30 } : { top: 100, bottom: 130, left: 400, right: 400 },
      absolutePadding: true, duration: 700, maxZoom: 14.5,
    });
  }, [data]);
  const locate = (at: [number, number]) => mapRef.current?.flyTo({ center: at, zoom: 15, padding: window.innerWidth >= 900 ? { left: 370, right: step === 'preview' || step === 'review' ? 510 : 360, top: 100, bottom: 120 } : { left: 0, right: 0, top: 60, bottom: window.innerHeight * .48 } });
  const select = (r: Route) => { setSelectedId(r.id); setStation(null); fitRoute(r); };
  const begin = (kind: Draft['kind']) => {
    if (kind !== 'add' && !selected) return;
    const id = `custom:${crypto.randomUUID()}`;
    const route: Route = kind === 'add' ? { id, route_id: id, subroute_id: id, name: '', branch_name: '', direction: '0', direction_label: '去程', stop_ids: [], path: null,
      hourly: { weekday: null, saturday: null, sunday: null }, frequencies: { [band]: 4 } } : structuredClone(selected!);
    const next: Draft = { kind, route, original: kind === 'add' ? null : structuredClone(selected!), band };
    setDraft(next); setEdits([route]); setEditIndex(0); setInteraction('stops'); setStation(null); setStopQuery(''); setInsertAfter(-1);
    setStep(kind === 'delete' ? 'preview' : 'edit');
    if (route.stop_ids.length) fitRoute(route);
  };
  const openedTarget = useRef(false);
  useEffect(() => {
    if (!target || openedTarget.current) return;
    openedTarget.current = true;
    if (selected?.id === target.variant) begin('edit');
    else setMessage('這條路線已在本機方案中刪除，請先復原刪除動作。');
  });
  const changeRoute = (next: Route) => {
    if (!draft) return;
    setDraft({ ...draft, route: next });
    const nextEdits = [...edits.slice(0, editIndex + 1), next].slice(-60);
    setEdits(nextEdits); setEditIndex(nextEdits.length - 1);
  };
  const undoDraft = (index: number) => { if (draft && edits[index]) { setEditIndex(index); setDraft({ ...draft, route: edits[index] }); setInsertAfter(-1); setInteraction('stops'); } };
  const insertIndex = draft ? Math.min(insertAfter < 0 ? draft.route.stop_ids.length : insertAfter + 1, draft.route.stop_ids.length) : 0;
  const addStop = (id: string) => {
    if (!draft || step !== 'edit' || !data.records.stops[id]) return;
    const ids = [...draft.route.stop_ids];
    if (ids.length >= 300) { setMessage('單向路線最多 300 個停靠站。'); return; }
    const at = insertIndex;
    if (ids[at - 1] === id || ids[at] === id) { setMessage('這個位置已有相同停靠站。'); return; }
    ids.splice(at, 0, id); changeRoute({ ...draft.route, stop_ids: ids, path: null });
    setInsertAfter(at); setStopQuery(''); setStation(null); setMessage(`已加入 ${data.records.stops[id].name}`);
  };
  const moveStop = (from: number, to: number) => {
    if (!draft || to < 0 || to >= draft.route.stop_ids.length || from === to) return;
    const ids = [...draft.route.stop_ids]; ids.splice(to, 0, ids.splice(from, 1)[0]);
    changeRoute({ ...draft.route, stop_ids: ids, path: null }); setInsertAfter(-1);
  };
  const apply = () => {
    if (!draft || busy || error || !result) return;
    const next = [...history, { ...draft, speed: settings.speed, dwell: settings.dwell, name: draft.route.name, before: changes, after: evaluated }].slice(-20);
    setHistory(next); setChanges(evaluated); setSelectedId(draft.route.id); setBand(draft.band); clearDraft();
    setMessage(saveScenario(data, evaluated, next) ? '已套用並儲存至此裝置，可再次檢視影響或復原。' : '已套用；此瀏覽器無法保存，重新整理後方案會遺失。');
  };
  const restore = () => {
    if (!last) return;
    const next = history.slice(0, -1);
    setHistory(next); setChanges(last.before); setSelectedId(last.route.id); clearDraft();
    setMessage(saveScenario(data, last.before, next) ? '已復原上一次變更。' : '已復原；無法保存至此裝置。');
  };
  const f = draft ? frequency(draft.route, draft.band) : null;
  const valid = !!draft && draft.route.name.trim().length > 0 && new Set(draft.route.stop_ids).size >= 2
    && (f === null || (Number.isFinite(f) && f >= 0 && f <= 60));
  const groups = useMemo(() => {
    const grouped = new Map<string, Route[]>();
    for (const r of routes) grouped.set(r.route_id, [...(grouped.get(r.route_id) ?? []), r]);
    return [...grouped.values()].sort((a, b) => Number(b[0].route_id.startsWith('custom:')) - Number(a[0].route_id.startsWith('custom:')) || a[0].name.localeCompare(b[0].name, 'zh-Hant', { numeric: true }));
  }, [routes]);
  const matches = useMemo(() => searchRouteGroups(groups, query), [groups, query]);
  const stopMatches = useMemo(() => stopQuery.trim() ? Object.entries(data.records.stops).filter(([id, s]) => `${s.name} ${id}`.includes(stopQuery.trim())).slice(0, 20) : [], [data, stopQuery]);
  const activeRoute = activeDraft?.route ?? selected;
  const editor: EditorMapProps = { data, grid: raster, route: activeRoute, original: activeDraft?.original ?? null, deleting: activeDraft?.kind === 'delete', showStopNumbers: step !== 'overview',
    interaction: step === 'edit' ? interaction : 'select', insertIndex, gridLines: settings.gridLines, bike: settings.bike,
    existingServices, highlightedServiceRouteId: activeServiceRouteId,
    onStop: addStop, onRoute: (id) => { if (step === 'overview') { const r = routes.find((r) => r.route_id === id); if (r) select(r); } },
    onPath: (path) => draft && changeRoute({ ...draft.route, path }) };
  const mapState = useMemo((): AppState => ({ ...state, mode: 'frequency', labels: settings.labels,
    layers: new Set(['routes', 'stops', 'mrt', 'places', ...(settings.bike ? ['youbike' as const] : [])]) }), [state, settings]);
  const removed = draft?.original?.stop_ids.filter((id) => !draft.route.stop_ids.includes(id)) ?? [];
  const added = draft?.route.stop_ids.filter((id) => !draft.original?.stop_ids.includes(id)) ?? [];

  return <div ref={stageRef} className={`route-workspace route-step-${step}`}>
    <MapView mapRef={mapRef} initialView={initialView} data={map} grid={grid} gridData={gridData} coverage={null} coverageImage={null} coverageLines={null}
      need={null} state={mapState} station={station} onStation={setStation} onCloseStation={() => setStation(null)} onReady={() => selected && fitRoute(selected)} onMoveEnd={() => {}} editor={editor} />
    <Search data={map} onChoose={(item) => {
      if (step === 'edit' && item.station?.type === 'bus' && interaction === 'stops' && data.records.stops[item.station.id]) addStop(item.station.id);
      else setStation(item.station ?? null);
      locate(item.at);
    }} />
    <div className="route-context"><label><span className="sr-only">評估時段</span><select aria-label="評估時段" value={effectiveBand} disabled={step === 'review'} onChange={(e) => {
      const next = e.target.value as Band; setBand(next); if (draft) setDraft({ ...draft, band: next });
    }}>{BANDS.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}</select></label><span>公車／捷運 350 m</span></div>

    {step === 'overview' && <>
      <aside className="card route-sidebar">
        <div className="route-sidebar-head"><p className="route-eyebrow">規劃方案 / 臺北市</p><h1 tabIndex={-1}>路線編輯</h1>
          <p className="muted">調整站點與班次，<br />先看影響，再保留方案。</p><button className="route-primary" onClick={() => begin('add')}>＋ 新增路線</button></div>
        <label className="route-search-label">現況路線<input type="search" placeholder="搜尋路線名稱、附線或來源 ID" value={query} onChange={(e) => setQuery(e.target.value)} /></label>
        <div className="route-section-title"><b>{query.trim() ? '搜尋結果' : '路線清單'}</b><small>{matches.length}／{groups.length} 條</small>{query && <button className="link-btn" onClick={() => setQuery('')}>清除搜尋</button>}</div>
        <p className="route-note">人口試算使用工具內全體路網與目前方案，範圍限臺北市；搜尋或選取路線不改變評估範圍。搜尋以名稱優先，也比對附線名稱與來源 ID。</p>
        <div className="route-list" aria-label="現況路線清單">
          {matches.map(({ routes: g, reason }) => <button key={g[0].route_id} className={`route-list-item${selected?.route_id === g[0].route_id ? ' is-selected' : ''}`} onClick={() => select(g.find((r) => r.id === selectedId) ?? g[0])}>
            <span><b>{g[0].name}</b><small>{g.some((r) => changes[r.id]) ? '方案已調整' : `${g.length} 個附線方向`}</small>{reason && <small>{reason}</small>}</span><em>{rate(frequency(g.find((r) => r.id === selectedId) ?? g[0], band))}<small> 班/h</small></em>
          </button>)}
          {!matches.length && <p className="route-note">找不到路線，請換個名稱或編號。</p>}
        </div>
        <div className="route-sidebar-bottom">
          {selected && <><label>已選取：{selected.name}<select aria-label="選擇附線與方向" value={selected.id} onChange={(e) => { const r = routes.find((r) => r.id === e.target.value); if (r) select(r); }}>
            {routes.filter((r) => r.route_id === selected.route_id).map((r) => <option key={r.id} value={r.id}>{r.branch_name || r.name} · {r.direction_label} · {r.stop_ids.length} 站</option>)}
          </select></label><div className="route-actions"><button className="route-primary" onClick={() => begin('edit')}>編輯路線</button><button className="btn-outline route-negative" onClick={() => begin('delete')}>刪除路線</button></div></>}
          {last && <div className="route-applied"><b>已套用：{last.name}</b><div className="route-actions"><button className="link-btn" onClick={() => { setStep('review'); fitRoute(last.route); }}>再次檢視影響</button><button className="link-btn route-negative" onClick={restore}>復原變更</button></div></div>}
          <p className="route-note">變更儲存在此裝置的模擬方案。現況路網作為固定比較基準；班表未分方向，以相同班表試算各方向。</p>
        </div>
      </aside>
      <PopulationSummary result={result} applied={Object.keys(changes).length > 0} busy={busy} />
      {error && <div className="route-map-alert" role="alert">{error} <button onClick={() => setRetry((n) => n + 1)}>重試</button></div>}
    </>}

    {step === 'edit' && draft && <>
      <aside className="card route-sidebar">
        <div className="route-sidebar-head"><button className="btn-outline" onClick={() => leave(clearDraft)}>← 路線工具</button><h1 tabIndex={-1}>{draft.kind === 'add' ? '新增路線' : `編輯${draft.original?.name}`}</h1>
          <p className="route-step-label">1 調整路線　→　2 預覽影響</p>
          <label>路線名稱<input maxLength={100} value={draft.route.name} placeholder="為這條路線命名" onChange={(e) => changeRoute({ ...draft.route, name: e.target.value })} /></label>
          <div className="route-field-row"><label>編輯方向<input aria-label="編輯方向" maxLength={60} value={draft.route.direction_label} onChange={(e) => changeRoute({ ...draft.route, direction_label: e.target.value })} /></label>
            <label>每小時班次<input aria-label="每小時班次" type="number" min={0} max={60} step={.5} placeholder="未提供" value={f === null || !Number.isFinite(f) ? '' : Number(f.toFixed(2))} onChange={(e) => changeRoute({ ...draft.route, frequencies: { ...draft.route.frequencies, [draft.band]: e.target.value === '' ? NaN : Number(e.target.value) } })} /></label></div>
          <p className="route-note">{draft.kind === 'add' ? '單向路線；未設定時段的班次為未知。' : '僅編輯此附線方向，其餘方向維持。'} {BANDS.find((b) => b.id === draft.band)?.label}</p>
        </div>
        <div className="route-section-title"><h3>停靠站序・{draft.original ? `${draft.original.stop_ids.length} → ` : ''}{draft.route.stop_ids.length} 站</h3><button className="link-btn" onClick={() => fitRoute(draft.route)}>顯示全線</button></div>
        <p className="route-note">點選地圖藍框公車站加入；拖曳清單調整站序。</p>
        <label className="route-stop-search">加入公車站<input type="search" value={stopQuery} placeholder="搜尋站名或站位編號" onChange={(e) => setStopQuery(e.target.value)} /></label>
        {stopQuery && <div className="route-stop-results" aria-label="可加入站點">{stopMatches.map(([id, s]) => <button key={id} onClick={() => { addStop(id); locate(s.at); }}>{s.name}<small>#{id}</small><b>＋</b></button>)}{!stopMatches.length && <p>找不到公車站</p>}</div>}
        <label className="route-insert-label">插入位置<select value={insertAfter} onChange={(e) => setInsertAfter(Number(e.target.value))}><option value={-1}>路線末端</option>{draft.route.stop_ids.map((id, i) => <option key={i} value={i}>第 {i + 1} 站 {data.records.stops[id].name} 之後</option>)}</select></label>
        <ol className="route-stop-list">
          {draft.route.stop_ids.map((id, i) => <li key={`${i}:${id}`} id={`route-stop-${i}`} draggable onDragStart={(e) => { setDragIndex(i); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(i)); }} onDragEnd={() => setDragIndex(null)} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (dragIndex !== null) moveStop(dragIndex, i); setDragIndex(null); }} className={draft.original && !draft.original.stop_ids.includes(id) ? 'is-added' : ''}>
            <span className="route-drag" aria-hidden="true">⠿</span><span className="route-number">{i + 1}</span><button className="route-stop-name" onClick={() => locate(data.records.stops[id].at)}>{data.records.stops[id].name}</button>
            <div className="route-stop-actions"><button disabled={i === 0} aria-label={`上移第 ${i + 1} 站`} onClick={() => moveStop(i, i - 1)}>↑</button><button disabled={i === draft.route.stop_ids.length - 1} aria-label={`下移第 ${i + 1} 站`} onClick={() => moveStop(i, i + 1)}>↓</button><button aria-label={`取消停靠第 ${i + 1} 站`} onClick={() => { changeRoute({ ...draft.route, stop_ids: draft.route.stop_ids.filter((_, k) => k !== i), path: null }); setInsertAfter(-1); }}>×</button></div>
          </li>)}
          {!draft.route.stop_ids.length && <li className="route-empty">點選地圖上的藍框圓點加入第一站，<br />也可搜尋站名；至少需要 2 個不同站點。</li>}
        </ol>
        {!!removed.length && <details className="route-removed"><summary>取消停靠 {removed.length} 站</summary>{removed.map((id, i) => <div key={i}><s>{data.records.stops[id].name}</s><button className="link-btn" onClick={() => addStop(id)}>加回</button></div>)}</details>}
        <div className="route-sidebar-bottom"><Duration draft={draft} data={data} speed={settings.speed} dwell={settings.dwell} />
          {!valid && <p className="route-note" role="status">請填路線名稱、加入至少 2 個不同站點，班次須為 0–60。</p>}
          <button className="route-primary" disabled={!valid} onClick={() => { setStation(null); setStep('preview'); }}>預覽影響 →</button></div>
      </aside>
      <div className="route-toolbar" role="toolbar" aria-label="路線調整工具">
        <button aria-pressed={interaction === 'stops'} onClick={() => setInteraction('stops')}>選擇站點</button><button aria-pressed={interaction === 'path'} disabled={draft.route.stop_ids.length < 2} onClick={() => { setInteraction('path'); if (!draft.route.path) changeRoute({ ...draft.route, path: routePath(draft.route, data) }); }}>調整路徑</button>
        <button disabled={editIndex === 0} onClick={() => undoDraft(editIndex - 1)}>復原</button><button disabled={editIndex >= edits.length - 1} onClick={() => undoDraft(editIndex + 1)}>重做</button>
      </div>
      <p className="route-map-hint">{interaction === 'stops'
        ? <><span className="route-stop-key" aria-hidden="true" />藍框圓點可選取 · 點擊加入第 {insertIndex + 1} 站 · 已選站點顯示編號</>
        : '拖曳控制點調整路徑；＋新增點，方向鍵微調，Delete 移除。'}<br />站序變動會重建連線；圖上路徑不代表可行駛導航。</p>
      {existingServices !== null ? <ExistingServicesPanel data={data} services={existingServices} selectedStopIds={draft.route.stop_ids} band={effectiveBand}
        draft={draft} draftFrequency={f} applied={Object.keys(changes).length > 0} highlightedRouteId={activeServiceRouteId} onHighlight={setHighlightedServiceRouteId} />
        : <section className="card route-draft-summary"><h3>{draft.route.name || '未命名路線'}・草稿</h3><span className="route-tag">{ACTION_LABELS[draft.kind]}</span>
        <b>{draft.route.direction_label}</b><p>{draft.route.stop_ids.length} 個停靠站 · 每小時 {rate(f)} 班</p>
        {draft.original && <p>新增 {added.length} 站／取消 {removed.length} 站</p>}<p className="route-note">人口與設施影響請至下一步比較。</p></section>}
    </>}

    {(step === 'preview' || step === 'review') && activeDraft && <>
      <section className="card route-change-summary"><p className="route-eyebrow">{step === 'review' ? '已套用方案' : '本次模擬'}</p><h1 tabIndex={-1}>{activeDraft.route.name}</h1><span className={`route-tag ${activeDraft.kind === 'delete' ? 'loss' : ''}`}>{ACTION_LABELS[activeDraft.kind]}</span>
        <p>{activeDraft.kind === 'delete' ? '移除整條路線及所有附線去回程服務' : `${activeDraft.route.stop_ids.length} 個停靠站 · 每小時 ${rate(frequency(activeDraft.route, activeDraft.band))} 班`}</p>
        <p className="route-note">保留其他路線；人口以臺北市範圍去重。</p></section>
      <Impact key={`${step}:${activeDraft.route.id}`} draft={activeDraft} data={data} result={result} busy={busy} error={error} review={step === 'review'} speed={step === 'review' && last ? last.speed : settings.speed} dwell={step === 'review' && last ? last.dwell : settings.dwell}
        onBack={() => { if (step === 'review' || activeDraft.kind === 'delete') clearDraft(); else setStep('edit'); }} onApply={apply} onRetry={() => setRetry((n) => n + 1)} onLocate={locate} />
    </>}
    <section className="card route-legend"><b>{step === 'preview' || step === 'review' ? '影響差異' : '地圖圖例'}</b>
      <div><span className="route-legend-frequency">{['#ffe3c6', '#fbc48d', '#f5a459', '#ed8733', '#d76b20'].map((c, i) => <span key={c}><i style={{ background: c }} />{['1–3', '4–7', '8–15', '16–23', '24+'][i]}</span>)}<small>班/h</small></span>
        <span><i className="route-legend-mrt" />捷運／班次未知</span>{settings.bike && <span><i className="route-legend-bike" />YouBike 1 km</span>}<span><i className="route-legend-gain" />服務增加</span><span><i className="route-legend-loss" />服務減少（含班次）</span></div>
      <small>100 m 網格 · 公車橙色深淺依班次，不隨距離衰減</small></section>
    {message && <div className="route-toast" role="status">{message}</div>}

    {dialog === 'discard' && <Dialog title="離開前，保留這次調整？" onClose={() => setDialog(null)}><p>站序與班次尚未套用至方案。繼續編輯可保留目前的停靠點與設定。</p><div className="route-actions"><button className="route-primary" onClick={() => setDialog(null)}>繼續編輯</button><button className="btn-outline route-negative" onClick={() => { const done = exit.current; setDialog(null); clearDraft(); done?.(); }}>放棄變更</button></div></Dialog>}
    {dialog === 'metrics' && <Dialog title="怎麼看影響？" onClose={() => setDialog(null)}><h3>可達人口</h3><p>臺北市同一時段，以 100 m 格心距公車／捷運站 350 m 判斷，2024-12 戶籍人口按統計區面積分攤並去重。新增、失去與淨變化分列；班次降低仍可達的居民另外列出。區間包含缺班表的可能覆蓋。</p><h3>高齡與學生</h3><p>65 歲以上與就學身分資料未接入，顯示未估算；不以年齡代理學生，兩個族群也不能相加。</p><h3>設施與時間</h3><p>比較最近可服務站距與 350 m 內公車班次；捷運無班表，不加造班次。草圖單程以偏好的速度、停站秒數估算，無路況與導航驗證；刪除後不適用。</p><h3>班表與範圍</h3><p>班表未分方向，假設同一附線班表套用各方向；同一附線方向同格去重。僅臺北市公運處路線，不能代表完整雙北。YouBike 1 km 是參考，不加入步行可達人口。</p><button className="route-primary" onClick={() => setDialog(null)}>了解</button></Dialog>}
    {dialog === 'preferences' && <Dialog title="路線工具偏好" onClose={() => setDialog(null)}><p>評估固定使用 100 m 網格、公車／捷運 350 m；探索地圖的偏好另行保留。</p><div className="route-preferences">
      <label><input type="checkbox" checked={prefDraft.gridLines} onChange={(e) => setPrefDraft({ ...prefDraft, gridLines: e.target.checked })} />顯示網格線</label><label><input type="checkbox" checked={prefDraft.bike} onChange={(e) => setPrefDraft({ ...prefDraft, bike: e.target.checked })} />顯示 YouBike 1 km 參考</label><label><input type="checkbox" checked={prefDraft.labels} onChange={(e) => setPrefDraft({ ...prefDraft, labels: e.target.checked })} />顯示底圖地名</label>
      <label>草圖平均速度（km/h）<input type="number" min={5} max={60} value={prefDraft.speed} onChange={(e) => setPrefDraft({ ...prefDraft, speed: Number(e.target.value) })} /></label><label>每站停靠（秒）<input type="number" min={0} max={180} value={prefDraft.dwell} onChange={(e) => setPrefDraft({ ...prefDraft, dwell: Number(e.target.value) })} /></label></div>
      <div className="route-actions"><button className="btn-outline" onClick={() => setDialog(null)}>取消</button><button className="route-primary" disabled={!Number.isFinite(prefDraft.speed) || prefDraft.speed < 5 || prefDraft.speed > 60 || !Number.isFinite(prefDraft.dwell) || prefDraft.dwell < 0 || prefDraft.dwell > 180} onClick={() => { setSettings(prefDraft); setDialog(null); }}>套用設定</button></div></Dialog>}
  </div>;
});
