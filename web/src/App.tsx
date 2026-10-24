import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { MapRef } from 'react-map-gl/maplibre';
import AboutDialog from './components/AboutDialog';
import Panel from './components/Panel';
import Preferences from './components/Preferences';
import Search, { type SearchItem } from './components/Search';
import Topbar from './components/Topbar';
import { DAY_LABELS, NEED_BANDS, type Quadrant } from './config';
import { coverageGridLines, coverageImage } from './coverage';
import { gridCollection, hourText, loadCoverage, loadGrid, loadMapData, loadNeed } from './data';
import { readHash, writeHash } from './hashState';
import MapView from './map/MapView';
import { computeNeed, needCollection, quadrantOf } from './need';
import type { AppState, Coverage, Grid, MapData, NeedGrid, ViewState } from './types';

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
  const [data, setData] = useState<MapData | null>(null);
  const [grid, setGrid] = useState<Grid | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [playing, setPlaying] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [explainerOpen, setExplainerOpen] = useState(true);
  const [prefsOpen, setPrefsOpen] = useState(false);

  const update = useCallback((patch: Partial<AppState>) => setState((s) => ({ ...s, ...patch })), []);

  useEffect(() => {
    loadMapData().then(setData, setError);
  }, []);

  useEffect(() => {
    let current = true;
    loadGrid(state.cell).then((g) => current && setGrid(g), setError);
    return () => { current = false; };
  }, [state.cell]);

  // Coverage data is only fetched once the coverage mode is first opened, one file per cell size.
  useEffect(() => {
    if (state.mode !== 'coverage') return undefined;
    let current = true;
    loadCoverage(state.coverageCell).then((c) => current && setCoverage(c), setError);
    return () => { current = false; };
  }, [state.mode, state.coverageCell]);

  useEffect(() => {
    if (state.mode === 'need' && !needGrid) loadNeed().then(setNeedGrid, setError);
  }, [state.mode, needGrid]);

  useEffect(() => writeHash(state, viewRef.current), [state]);

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
  const frequency = state.mode === 'frequency';
  const needMode = state.mode === 'need';

  const handleMoveEnd =useCallback((view: ViewState) => {
    viewRef.current = view;
    writeHash(state, view);
  }, [state]);

  const handleSearch = (item: SearchItem) => {
    if (item.layer) {
      const layers = new Set(state.layers).add(item.layer);
      const places = item.place ? new Set(state.places).add(item.place) : state.places;
      update({ layers, places });
    }
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

  const loading = !error && !(data && gridData && mapReady);

  return (
    <>
      <Topbar mode={state.mode} onAbout={() => setAboutOpen(true)} aboutActive={aboutOpen} onPreferences={() => setPrefsOpen(true)} onShare={share} />
      <main className="stage">
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
            state={state}
            onReady={() => setMapReady(true)}
            onMoveEnd={handleMoveEnd}
          />
        )}
        <Panel state={state} playing={playing} needCounts={need?.counts ?? null} onChange={update} onTogglePlay={() => setPlaying((p) => !p)} onPreferences={() => setPrefsOpen(true)} />
        {data && <Search data={data} onChoose={handleSearch} />}
        <div className="status-chip">
          {frequency ? `${DAY_LABELS[state.day]} · ${hourText(state.hour)}`
            : needMode ? `需求落差 · ${NEED_BANDS.find((b) => b.key === state.band)!.label}`
            : '站點覆蓋 · 直線距離，不分時段'}
        </div>
        <section className={`card explainer${explainerOpen ? '' : ' is-collapsed'}`}>
          <div className="explainer-head">
            <h3>{frequency ? '班次網格怎麼看？' : needMode ? '需求落差怎麼看？' : '從站心出發，看見步行與騎乘的不同尺度。'}</h3>
            <button className="icon-btn" aria-label={explainerOpen ? '收合說明' : '展開說明'} onClick={() => setExplainerOpen((o) => !o)}>ⓘ</button>
          </div>
          {needMode
            ? <p>每格約 250 m。需求由居住人口、重要地點（依時段加權）與附近捷運進出量的排名加權而成；供給由附近公車班次與捷運距離算出。兩者都是同一時段內的排名，橘色偏「需求高、供給低」，藍色偏「供給多於需求」。這是研究草案，權重是假設值，不是實際搭乘量。</p>
            : frequency
            ? <p>每格約 100、150、250 或 500 m，數值為格內公車站牌所屬附線在該小時的計畫發車數加總（同一附線只計一次、不分方向）。以起站發車時刻歸入小時，未計行駛時間；不代表實際載客或步行可達性。</p>
            : <p>每格約 {state.coverageCell} m，依格心到最近站點的直線距離上色：橙色是走到公車、捷運或 YouBike 站，青綠是從 YouBike 站騎出去。兩層都只是半徑示意，未計入路網、河川阻隔、班次多寡與即時可借車輛。</p>}
        </section>
        {loading && <div className="loading">載入地圖資料中…</div>}
        {error && (
          <div className="card data-error">
            <b>找不到地圖資料</b>
            <p className="muted">請先在 repo 根目錄執行 <code>uv run scripts/build_web_data.py</code>。<br />{error.message}</p>
          </div>
        )}
      </main>
      {prefsOpen && <Preferences state={state} onApply={(patch) => { update(patch); setPrefsOpen(false); }} onClose={() => setPrefsOpen(false)} />}
      <AboutDialog open={aboutOpen} grid={grid} coverage={coverage} need={needGrid} onClose={() => setAboutOpen(false)} />
    </>
  );
}
