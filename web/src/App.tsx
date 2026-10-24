import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { MapRef } from 'react-map-gl/maplibre';
import { groupFor, villageAgeCollection, villageCenter, type VillageAges } from './age';
import AboutDialog from './components/AboutDialog';
import Panel from './components/Panel';
import Preferences from './components/Preferences';
import Search, { type SearchItem } from './components/Search';
import Topbar from './components/Topbar';
import { DAY_LABELS, NEED_BANDS, type Quadrant } from './config';
import { coverageGridLines, coverageImage } from './coverage';
import { EMPTY_SUPPLEMENTAL, gridCollection, hourText, loadCoverage, loadGrid, loadMapData, loadNeed, loadVillageAges, loadSupplementalLayer, SUPPLEMENTAL_FILES, type SupplementalKey } from './data';
import { readHash, writeHash } from './hashState';
import MapView from './map/MapView';
import RouteWorkspace, { type RouteWorkspaceHandle } from './routeEditor/RouteWorkspace';
import ImprovementList, { type ImprovementHandle } from './improvements/ImprovementList';
import { PERIODS, type Filters } from './improvements/types';
import type { RouteTarget } from './routeEditor/RouteWorkspace';
import { computeNeed, needCollection, quadrantOf } from './need';
import { villagePeriod } from './regional';
import type { AppState, CoreMapData, Coverage, Grid, NeedGrid, StationSelection, SupplementalData, ViewState } from './types';

const initial = readHash();

export default function App() {
  const mapRef = useRef<MapRef>(null);
  const viewRef = useRef<ViewState>({ center: initial.center, zoom: initial.zoom });
  const [state, setState] = useState<AppState>(() => {
    const { center: _center, zoom: _zoom, ...rest } = initial;
    return rest;
  });
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [needGrid, setNeedGrid] = useState<NeedGrid | null>(null);
  const [villageAges, setVillageAges] = useState<VillageAges | null>(null);
  const [ageError, setAgeError] = useState<Error | null>(null);
  const [ageAttempt, setAgeAttempt] = useState(0);
  const [selectedVillage, setSelectedVillage] = useState<string | null>(null);
  const [baseData, setBaseData] = useState<CoreMapData | null>(null);
  const [supplemental, setSupplemental] = useState<SupplementalData>(EMPTY_SUPPLEMENTAL);
  const [supplementalPending, setSupplementalPending] = useState<Set<SupplementalKey>>(new Set());
  const [supplementalAttempt, setSupplementalAttempt] = useState(0);
  const supplementalRequested = useRef(new Set<SupplementalKey>());
  const supplementalFailed = useRef(new Set<SupplementalKey>());
  const [dataAttempt, setDataAttempt] = useState(0);
  const data = useMemo(() => baseData ? { ...baseData, ...supplemental } : null, [baseData, supplemental]);
  const [grid, setGrid] = useState<Grid | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [analysisError, setAnalysisError] = useState<Error | null>(null);
  const [playing, setPlaying] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [explainerOpen, setExplainerOpen] = useState(true);
  const [prefsOpen, setPrefsOpen] = useState(false);
  const [station, setStation] = useState<StationSelection | null>(null);
  const [page, setPage] = useState<'explore' | 'routes' | 'improvements'>(() => {
    const value = new URLSearchParams(window.location.hash.slice(1)).get('page');
    return value === 'improvements' || value === 'routes' ? value : 'explore';
  });
  const routeTool = page === 'routes';
  const [routeTarget, setRouteTarget] = useState<RouteTarget | null>(null);
  const improvementRef = useRef<ImprovementHandle>(null);
  const editorRef = useRef<RouteWorkspaceHandle>(null);
  const stationOrigin = useRef<HTMLElement | null>(null);
  const villageOrigin = useRef<HTMLElement | null>(null);
  const focusFrame = useRef<number | null>(null);

  const selectStation = useCallback((next: StationSelection | null, origin?: HTMLElement) => {
    // A new selection supersedes any focus restoration from the previous card.
    if (focusFrame.current !== null) cancelAnimationFrame(focusFrame.current);
    focusFrame.current = null;
    if (next) stationOrigin.current = origin ?? document.activeElement as HTMLElement;
    setStation(next);
    if (next) setSelectedVillage(null);
  }, []);
  const closeStation = useCallback(() => {
    setStation(null);
    const origin = stationOrigin.current;
    if (focusFrame.current !== null) cancelAnimationFrame(focusFrame.current);
    focusFrame.current = requestAnimationFrame(() => {
      focusFrame.current = null;
      if (origin?.isConnected) origin.focus({ preventScroll: true });
      else mapRef.current?.getCanvas().focus({ preventScroll: true });
    });
  }, []);

  const update = useCallback((patch: Partial<AppState>) => setState((s) => ({ ...s, ...patch })), []);

  useEffect(() => {
    let current = true;
    setError(null);
    loadMapData().then((value) => { if (current) setBaseData(value); }, (reason: Error) => { if (current) setError(reason); });
    return () => { current = false; };
  }, [dataAttempt]);

  useEffect(() => {
    const wanted: SupplementalKey[] = ['regional_bus_routes', 'regional_bus_stops', 'supplemental_manifest'];
    if (state.layers.has('villages') || station || routeTool) wanted.push('villages');
    if (state.layers.has('traffic')) wanted.push('traffic_sections');
    if (state.layers.has('bus_times')) wanted.push('bus_travel_times');
    for (const key of wanted) {
      if (supplementalRequested.current.has(key)) continue;
      supplementalRequested.current.add(key);
      setSupplementalPending((previous) => new Set(previous).add(key));
      loadSupplementalLayer(key).then((value) => {
        setSupplemental((previous) => ({ ...previous, [key]: value }));
        supplementalFailed.current.delete(key);
      }, (reason: unknown) => {
        supplementalFailed.current.add(key);
        const message = `${SUPPLEMENTAL_FILES[key]}：${reason instanceof Error ? reason.message : '下載失敗'}`;
        setSupplemental((previous) => ({ ...previous, supplemental_errors: [...previous.supplemental_errors, message] }));
      }).finally(() => setSupplementalPending((previous) => {
        const next = new Set(previous); next.delete(key); return next;
      }));
    }
  }, [state.layers, station, routeTool, supplementalAttempt]);

  const retrySupplemental = () => {
    for (const key of supplementalFailed.current) supplementalRequested.current.delete(key);
    setSupplemental((previous) => ({ ...previous, supplemental_errors: [] }));
    setSupplementalAttempt((value) => value + 1);
  };

  useEffect(() => {
    let current = true;
    loadGrid(state.cell).then((g) => { if (current) { setGrid(g); setAnalysisError(null); } }, (reason: Error) => { if (current) setAnalysisError(reason); });
    return () => { current = false; };
  }, [state.cell]);

  // Coverage data is only fetched once the coverage mode is first opened, one file per cell size.
  useEffect(() => {
    if (state.mode !== 'coverage') return undefined;
    let current = true;
    loadCoverage(state.coverageCell).then((c) => { if (current) { setCoverage(c); setAnalysisError(null); } }, (reason: Error) => { if (current) setAnalysisError(reason); });
    return () => { current = false; };
  }, [state.mode, state.coverageCell]);

  useEffect(() => {
    if (state.mode === 'need' && !needGrid) loadNeed().then((value) => { setNeedGrid(value); setAnalysisError(null); }, setAnalysisError);
  }, [state.mode, needGrid]);

  useEffect(() => {
    if (state.mode !== 'village_age' || villageAges) return;
    let current = true;
    loadVillageAges().then((value) => {
      if (current) { setVillageAges(value); setAgeError(null); }
    }, (reason: unknown) => {
      if (current) setAgeError(reason instanceof Error ? reason : new Error(String(reason)));
    });
    return () => { current = false; };
  }, [state.mode, villageAges, ageAttempt]);

  useEffect(() => writeHash(state, viewRef.current), [state]);
  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.slice(1));
    params.set('page', page); window.history.replaceState(null, '', `#${params}`);
  }, [page]);
  const improvementTime = useCallback((filters: Filters) => {
    const hour = filters.period === 'all' ? 24 : PERIODS[filters.period].start;
    setState((s) => s.day === filters.day && s.hour === hour ? s : { ...s, day: filters.day, hour });
  }, []);

  useEffect(() => {
    if (!playing) return undefined;
    const timer = setInterval(() => setState((s) => ({ ...s, hour: s.hour >= 23 ? 0 : s.hour + 1 })), 900);
    return () => clearInterval(timer);
  }, [playing]);

  const gridData = useMemo(
    () => (grid && grid.cell_m === state.cell ? gridCollection(grid, state) : null),
    [grid, state],
  );

  const { coverage: coverageLayers, walkTypes, walkRadii, rideRadius } = state;
  const coverageRaster = useMemo(
    () => (coverage ? coverageImage(coverage, { coverage: coverageLayers, walkTypes, walkRadii, rideRadius }) : null),
    [coverage, coverageLayers, walkTypes, walkRadii, rideRadius],
  );
  const coverageLines = useMemo(() => (coverage ? coverageGridLines(coverage) : null), [coverage]);
  const { band, needView, weights } = state;
  const need = useMemo(() => {
    if (!needGrid) return null;
    const result = computeNeed(needGrid, { band, weights });
    const counts = { priority: 0, saturated: 0, oversupplied: 0, low_low: 0 } as Record<Quadrant, number>;
    result.rDemand.forEach((d, i) => {
      const q = quadrantOf(d, result.rSupply[i]);
      if (q) counts[q]++;
    });
    return { grid: needGrid, result, counts, data: needCollection(needGrid, result, { band, needView, weights }) };
  }, [needGrid, band, needView, weights]);
  const villageData = useMemo(() => villageAges ? villageAgeCollection(villageAges, groupFor(villageAges.metadata, state.ageGroup, state.ageSet)) : null, [villageAges, state.ageGroup, state.ageSet]);
  const selectVillage = useCallback((id: string | null, fly = true) => {
    setSelectedVillage(id);
    if (id) {
      if (focusFrame.current !== null) cancelAnimationFrame(focusFrame.current);
      focusFrame.current = null;
      villageOrigin.current = fly ? document.activeElement as HTMLElement : mapRef.current?.getCanvas() ?? null;
      setStation(null);
      const feature = villageAges?.features.find((village) => village.properties.boundary_id === id);
      if (feature && fly) mapRef.current?.flyTo({ center: villageCenter(feature), zoom: 13.8 });
    }
  }, [villageAges]);
  const closeVillage = useCallback(() => {
    setSelectedVillage(null);
    if (focusFrame.current !== null) cancelAnimationFrame(focusFrame.current);
    focusFrame.current = requestAnimationFrame(() => {
      focusFrame.current = null;
      if (villageOrigin.current?.isConnected) villageOrigin.current.focus({ preventScroll: true });
      else mapRef.current?.getCanvas().focus({ preventScroll: true });
    });
  }, []);
  const frequency = state.mode === 'frequency';
  const ageMode = state.mode === 'village_age';
  const needMode = state.mode === 'need';
  const populationPeriod = villagePeriod(supplemental);

  const handleMoveEnd =useCallback((view: ViewState) => {
    viewRef.current = view;
    writeHash(state, view);
  }, [state]);

  const handleSearch = (item: SearchItem) => {
    if (item.layer) {
      const layers = new Set(state.layers).add(item.layer);
      const places = item.place ? new Set(state.places).add(item.place) : state.places;
      update({ layers, places, ...(item.layer === 'regional_stops' ? { leapfrogOnly: false } : {}) });
    }
    selectStation(item.station ?? null);
    mapRef.current?.flyTo({ center: item.at, zoom: item.zoom });
  };

  const share = async (): Promise<boolean> => {
    writeHash(state, viewRef.current);
    try {
      await navigator.clipboard.writeText(window.location.href);
      return true;
    } catch {
      return false;
    }
  };

  const loading = !error && !analysisError && !(data && gridData && mapReady);
  const navigate = (next: typeof page) => {
    const move = () => {
      if (next !== page) setMapReady(false);
      setPage(next); setStation(null); setPlaying(false); setAboutOpen(false);
    };
    if (routeTool && next !== 'routes' && editorRef.current) editorRef.current.leave(move); else move();
  };

  return (
    <>
      <Topbar mode={state.mode} onAbout={() => setAboutOpen(true)} aboutActive={aboutOpen}
        routeTool={routeTool} improvements={page === 'improvements'} onImprovements={() => navigate('improvements')}
        onExport={() => improvementRef.current?.exportCsv()}
        onRoutes={() => { setRouteTarget(null); navigate('routes'); }}
        onExplore={() => navigate('explore')}
        onIndicators={() => editorRef.current?.indicators()}
        onPreferences={() => routeTool ? editorRef.current?.preferences() : page === 'improvements' ? improvementRef.current?.preferences() : setPrefsOpen(true)} onShare={share} />
      <main className="stage">
        {page === 'improvements' ? <ImprovementList ref={improvementRef} map={data} onTime={improvementTime} onEdit={(variant, filters) => {
          setRouteTarget({ variant, day: filters.day, period: filters.period }); navigate('routes');
        }} /> : routeTool && data && grid && gridData ? <RouteWorkspace ref={editorRef} target={routeTarget} map={data} grid={grid} gridData={gridData} state={state} initialView={viewRef.current} /> : <>
        {data && grid && gridData && (
          <MapView
            mapRef={mapRef}
            initialView={viewRef.current}
            data={data}
            grid={grid}
            gridData={gridData}
            coverage={coverage}
            coverageImage={coverageRaster}
            coverageLines={coverageLines}
            need={need}
            villageAges={villageAges}
            villageData={villageData}
            selectedVillage={selectedVillage}
            onVillage={selectVillage}
            onCloseVillage={closeVillage}
            state={state}
            station={station}
            onStation={selectStation}
            onCloseStation={closeStation}
            onReady={() => setMapReady(true)}
            onMoveEnd={handleMoveEnd}
          />
        )}
        <Panel villageAges={villageAges} ageError={ageError} onRetryAges={() => { setAgeError(null); setAgeAttempt((attempt) => attempt + 1); }} selectedVillage={selectedVillage} onVillage={selectVillage} state={state} playing={playing} needCounts={need?.counts ?? null} data={data} supplementalPending={supplementalPending} onRetrySupplemental={retrySupplemental} onChange={update} onTogglePlay={() => setPlaying((p) => !p)} onPreferences={() => setPrefsOpen(true)} />
        {data && <Search data={data} onChoose={handleSearch} />}
        <div className="status-chip">
          {ageMode ? `村里年齡 · ${villageAges?.metadata.population_period ?? '載入中'} · 北北基`
            : state.layers.has('villages') ? `里${state.villageMetric === 'population' ? '人口' : '高齡占比'}${populationPeriod ? ` · ${populationPeriod}` : ''}`
            : frequency ? `${DAY_LABELS[state.day]} · ${hourText(state.hour)}`
            : needMode ? `需求落差 · ${NEED_BANDS.find((b) => b.key === state.band)!.label}`
            : '站點覆蓋 · 直線距離，不分時段'}
        </div>
        <section className={`card explainer${explainerOpen ? '' : ' is-collapsed'}`}>
          <div className="explainer-head">
            <h3>{ageMode ? '村里人口與站圈估算怎麼看？' : state.layers.has('villages') ? '里人口與年齡怎麼看？' : frequency ? '班次網格怎麼看？' : needMode ? '需求落差怎麼看？' : '從站心出發，看見步行與騎乘的不同尺度。'}</h3>
            <button className="icon-btn" aria-label={explainerOpen ? '收合說明' : '展開說明'} onClick={() => setExplainerOpen((o) => !o)}>ⓘ</button>
          </div>
          {ageMode
            ? <p>色彩是整個村里所選年齡層占戶籍人口的比例，灰色是未匹配、缺值或比例無分母。全國清冊與里界已另行核對，地圖只呈現北北基；里界版本與人口月份不同。點選站點可查看直線 350 m 內按村里面積分攤的年齡估算，只有涵蓋完整才顯示全站圈比例；人口不是實際搭乘量。</p>
            : state.layers.has('villages') ? <p>每個色塊是一個完整里，呈現{populationPeriod ? ` ${populationPeriod} ` : ''}戶籍人口或 65 歲以上人口占比；灰色表示缺值或代碼與名稱未核對。點選查看三個年齡組的人數。整里統計不是站點 350 m 人口估算；關閉此圖層即可恢復原分析填色。</p> : needMode
            ? <p>每格約 250 m。需求由居住人口、重要地點（依時段加權）與附近捷運進出量的排名加權而成；供給由附近公車班次與捷運距離算出。兩者都是同一時段內的排名，橘色偏「需求高、供給低」，藍色偏「供給多於需求」。這是研究草案，權重是假設值，不是實際搭乘量。</p>
            : frequency
            ? <p>每格約 100、150、250 或 500 m，數值為格內公車站牌所屬附線在該小時的計畫發車數加總（同一附線只計一次、不分方向）。以起站發車時刻歸入小時，未計行駛時間；不代表實際載客或步行可達性。</p>
            : <p>每格約 {state.coverageCell} m，依格心到最近站點的直線距離上色：橙色是走到公車、捷運或 YouBike 站，青綠是從 YouBike 站騎出去。兩層都只是半徑示意，未計入路網、河川阻隔、班次多寡與即時可借車輛。</p>}
        </section>
        {loading && <div className="loading">載入地圖資料中…</div>}
        {error && (
          <div className="card data-error">
            <b>地圖資料載入失敗</b>
            <p className="muted">請確認網路連線後重試。<br />{error.message}</p>
            <button className="retry-button" onClick={() => setDataAttempt((value) => value + 1)}>重新載入</button>
          </div>
        )}
        {analysisError && <div className="card data-error" role="alert"><b>分析資料載入失敗</b><p>{analysisError.message}</p><p className="muted">其他已載入圖層仍可操作；請重新載入頁面後重試。</p></div>}
        </>}
      </main>
      {prefsOpen && <Preferences state={state} data={data} supplementalPending={supplementalPending} onRetrySupplemental={retrySupplemental} onApply={(patch) => { update(patch); setPrefsOpen(false); }} onClose={() => setPrefsOpen(false)} />}
      <AboutDialog open={aboutOpen} grid={grid} coverage={coverage} need={needGrid} ages={villageAges} supplemental={supplemental} onClose={() => setAboutOpen(false)} />
    </>
  );
}
