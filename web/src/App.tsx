import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { MapRef } from 'react-map-gl/maplibre';
import AboutDialog from './components/AboutDialog';
import Panel from './components/Panel';
import Search, { type SearchItem } from './components/Search';
import Topbar from './components/Topbar';
import { DAY_LABELS } from './config';
import { gridCollection, hourText, loadGrid, loadMapData } from './data';
import { readHash, writeHash } from './hashState';
import MapView from './map/MapView';
import type { AppState, Grid, MapData, ViewState } from './types';

const initial = readHash();

export default function App() {
  const mapRef = useRef<MapRef>(null);
  const viewRef = useRef<ViewState>({ center: initial.center, zoom: initial.zoom });
  const [state, setState] = useState<AppState>({ day: initial.day, hour: initial.hour, cell: initial.cell, layers: initial.layers, places: initial.places });
  const [data, setData] = useState<MapData | null>(null);
  const [grid, setGrid] = useState<Grid | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [playing, setPlaying] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [explainerOpen, setExplainerOpen] = useState(true);

  const update = useCallback((patch: Partial<AppState>) => setState((s) => ({ ...s, ...patch })), []);

  useEffect(() => {
    loadMapData().then(setData, setError);
  }, []);

  useEffect(() => {
    let current = true;
    loadGrid(state.cell).then((g) => current && setGrid(g), setError);
    return () => { current = false; };
  }, [state.cell]);

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

  const handleMoveEnd = useCallback((view: ViewState) => {
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
      <Topbar onAbout={() => setAboutOpen(true)} aboutActive={aboutOpen} onShare={share} />
      <main className="stage">
        {data && grid && gridData && (
          <MapView
            mapRef={mapRef}
            initialView={viewRef.current}
            data={data}
            grid={grid}
            gridData={gridData}
            state={state}
            onReady={() => setMapReady(true)}
            onMoveEnd={handleMoveEnd}
          />
        )}
        <Panel state={state} playing={playing} onChange={update} onTogglePlay={() => setPlaying((p) => !p)} />
        {data && <Search data={data} onChoose={handleSearch} />}
        <div className="status-chip">{DAY_LABELS[state.day]} · {hourText(state.hour)}</div>
        <section className={`card explainer${explainerOpen ? '' : ' is-collapsed'}`}>
          <div className="explainer-head">
            <h3>班次網格怎麼看？</h3>
            <button className="icon-btn" aria-label={explainerOpen ? '收合說明' : '展開說明'} onClick={() => setExplainerOpen((o) => !o)}>ⓘ</button>
          </div>
          <p>每格約 250 或 500 m，數值為格內公車站牌所屬附線在該小時的計畫發車數加總（同一附線只計一次、不分方向）。以起站發車時刻歸入小時，未計行駛時間；不代表實際載客或步行可達性。</p>
        </section>
        {loading && <div className="loading">載入地圖資料中…</div>}
        {error && (
          <div className="card data-error">
            <b>找不到地圖資料</b>
            <p className="muted">請先在 repo 根目錄執行 <code>uv run scripts/build_web_data.py</code>。<br />{error.message}</p>
          </div>
        )}
      </main>
      <AboutDialog open={aboutOpen} grid={grid} onClose={() => setAboutOpen(false)} />
    </>
  );
}
