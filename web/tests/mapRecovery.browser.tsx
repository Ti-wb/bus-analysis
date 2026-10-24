// Manual integration fixture: npm run dev, then /tests/mapRecovery.browser.html
// ?fault=style rejects the external style only; ?fault=webgl simulates unavailable WebGL.
// These hooks exist only in this fixture and never in the application entry point.
import { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import App from '../src/App';
import type { MapRef } from 'react-map-gl/maplibre';
import { BASE_STYLE } from '../src/config';
import { EMPTY_SUPPLEMENTAL, gridCollection, loadGrid, loadMapData } from '../src/data';
import { readHash } from '../src/hashState';
import MapView from '../src/map/MapView';
import type { MapStatus } from '../src/map/loadState';
import type { StationSelection } from '../src/types';
import '../src/style.css';

const fault = new URLSearchParams(location.search).get('fault');
const blocked = { style: fault === 'style', webgl: fault === 'webgl' };
const originalFetch = window.fetch;
window.fetch = (input, init) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
  // Vite's relative base normally starts at index.html; this fixture lives under tests/.
  const resource = typeof input === 'string' && /^(?:\.\/)?data\//.test(input) ? new URL(input, `${location.origin}/`) : input;
  return blocked.style && url === BASE_STYLE
    ? Promise.reject(new Error('TEST: external style unavailable')) : originalFetch(resource, init);
};
const getContext = HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, ...args: Parameters<typeof getContext>) {
  if (blocked.webgl && String(args[0]).startsWith('webgl')) return null;
  return getContext.apply(this, args);
} as typeof getContext;
const initial = readHash();
const [core, grid] = await Promise.all([loadMapData(), loadGrid(initial.cell)]);
const data = { ...core, ...EMPTY_SUPPLEMENTAL };
const gridData = gridCollection(grid, initial);

function Fixture() {
  const mapRef = useRef<MapRef>(null);
  const savedCamera = useRef<ReturnType<typeof camera> | null>(null);
  const [status, setStatus] = useState<MapStatus>('loading');
  const [readyCount, setReadyCount] = useState(0);
  const [report, setReport] = useState('等待測試');
  const [events, setEvents] = useState<string[]>([]);
  const [station, setStation] = useState<StationSelection | null>(null);
  const savedCard = useRef<HTMLElement | null>(null);
  function camera() {
    const map = mapRef.current!;
    return { center: map.getCenter().toArray(), zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch(), padding: map.getPadding() };
  }
  function check(name: string, test: () => boolean) {
    try { setReport(`${test() ? 'PASS' : 'FAIL'}: ${name}`); }
    catch (error) { setReport(`FAIL: ${name}: ${String(error)}`); }
  }
  return <>
    <header style={{ position: 'absolute', zIndex: 20, background: 'white', padding: 12, width: '100%', display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      <strong>實際 MapView 故障模擬 · {fault ?? 'normal'} · {status} · ready 回呼 {readyCount}</strong>
      <button onClick={() => { blocked.style = blocked.webgl = false; setReport('已解除測試限制，可按地圖重試'); }}>解除測試限制</button>
      <button onClick={() => {
        mapRef.current!.jumpTo({ center: [121.531, 25.04], zoom: 13, bearing: 17, pitch: 25, padding: { top: 60, bottom: 40, left: 210, right: 80 } });
        savedCamera.current = camera(); setReport('已保存非零旋轉、傾斜與側欄 padding 的鏡頭');
      }}>設定並保存鏡頭</button>
      <button onClick={() => {
        const feature = data.mrt_stations.features.find((f) => f.properties.name === '台北車站') ?? data.mrt_stations.features[0];
        if (!feature) { setReport('FAIL: 測試資料缺少捷運站'); return; }
        const at = feature.geometry.coordinates as [number, number];
        mapRef.current!.jumpTo({ center: at, zoom: 15.5, bearing: 0, pitch: 0, padding: { top: 0, bottom: 0, left: 0, right: 0 } });
        setStation({ type: 'mrt', id: feature.properties.id, at, properties: feature.properties });
      }}>選取捷運站</button>
      <button onClick={() => {
        savedCard.current = document.querySelector<HTMLElement>('.station-card');
        setReport(savedCard.current ? '已保存站點卡；可展開設施後中斷並重試地圖' : 'FAIL: 請先選取捷運站');
      }}>保存站點卡</button>
      <button onClick={() => check('原站點卡保留且平移後跟隨標記', () => {
        const map = mapRef.current!, card = document.querySelector<HTMLElement>('.station-card');
        if (!station || !card || card !== savedCard.current) return false;
        const before = card.getBoundingClientRect().left, markerBefore = map.project(station.at).x;
        map.panBy([100, 0], { duration: 0 });
        const delta = map.project(station.at).x - markerBefore;
        return Math.abs(delta) > 1 && Math.abs(card.getBoundingClientRect().left - before - delta) < 1;
      })}>檢查站點卡平移</button>
      <button onClick={() => check('重試按鈕可直接點擊', () => {
        const button = document.querySelector<HTMLButtonElement>('.map-notice button');
        if (!button) return false;
        const rect = button.getBoundingClientRect();
        return button.contains(document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2));
      })}>檢查重試按鈕</button>
      <button onClick={() => mapRef.current!.getMap().fire('error', { error: new Error('TEST: data layer failure'), sourceId: 'bus-stops' })}>模擬圖層錯誤</button>
      <button onClick={() => {
        const gl = mapRef.current!.getCanvas().getContext('webgl2');
        const extension = gl?.getExtension('WEBGL_lose_context');
        if (extension) extension.loseContext(); else setReport('SKIP: WEBGL_lose_context 不可用');
      }}>模擬 WebGL 中斷</button>
      <button onClick={() => check('ready、只通知一次、無 loading', () => status === 'ready' && readyCount === 1 && !document.querySelector('.loading'))}>檢查成功狀態</button>
      <button onClick={() => check('failed 終態、無 loading、顯示重試', () => status === 'failed' && !document.querySelector('.loading') && !!document.querySelector('[role="alert"] button'))}>檢查失敗終態</button>
      <button onClick={() => check('圖層錯誤仍 ready、無 loading', () => status === 'ready' && !document.querySelector('.loading') && !!document.querySelector('.map-notice'))}>檢查圖層警告</button>
      <button onClick={() => check('完整鏡頭保留', () => {
        const saved = savedCamera.current, current = camera();
        return !!saved && [...saved.center, saved.zoom, saved.bearing, saved.pitch].every((value, i) => Math.abs(value - [...current.center, current.zoom, current.bearing, current.pitch][i]) < 1e-6)
          && JSON.stringify(saved.padding) === JSON.stringify(current.padding);
      })}>檢查鏡頭保留</button>
      <output style={{ width: '100%' }}>{report} · 狀態序列：{events.join(' → ')}</output>
    </header>
    <main style={{ position: 'absolute', inset: '145px 0 0' }}>
      <MapView mapRef={mapRef} initialView={initial} data={data} grid={grid} gridData={gridData} state={initial}
        coverage={null} coverageImage={null} coverageLines={null} need={null} station={station} onStation={setStation} onCloseStation={() => setStation(null)}
        onReady={() => setReadyCount((count) => count + 1)} onStatusChange={(next) => {
          setStatus(next); setEvents((previous) => previous.at(-1) === next ? previous : [...previous, next]);
        }} onMoveEnd={() => {}} />
      {status === 'loading' && <div className="loading">測試：載入中</div>}
    </main>
  </>;
}
function AppFixture() {
  const [report, setReport] = useState('整合模式：直接操作下方的正式 App');
  const saved = useRef<string | null>(null);
  const savedStatus = useRef<HTMLElement | null>(null);
  const form = () => JSON.stringify([...document.querySelectorAll<HTMLInputElement>('.route-workspace input')].map((input) => [input.placeholder, input.value]));
  return <>
    <App />
    <aside style={{ position: 'fixed', bottom: 0, left: 0, zIndex: 100, background: 'white', padding: 8, border: '2px solid #234', display: 'flex', flexWrap: 'wrap', maxWidth: '100%', gap: 8 }}>
      <button onClick={() => { blocked.style = blocked.webgl = false; setReport('已解除測試限制'); }}>解除測試限制</button>
      <button onClick={() => { saved.current = form(); setReport('已保存草稿輸入值'); }}>保存草稿輸入值</button>
      <button onClick={() => setReport(`${saved.current !== null && saved.current === form() ? 'PASS' : 'FAIL'}: 草稿輸入值保留`)}>檢查草稿保留</button>
      <button onClick={() => {
        const gl = document.querySelector<HTMLCanvasElement>('.maplibregl-canvas')?.getContext('webgl2');
        const extension = gl?.getExtension('WEBGL_lose_context');
        if (extension) extension.loseContext(); else setReport('SKIP: WEBGL_lose_context 不可用');
      }}>模擬 WebGL 中斷</button>
      <button onClick={() => setReport(`${!document.querySelector('.loading') && !!document.querySelector('.map-notice[role="alert"]') ? 'PASS' : 'FAIL'}: App 失敗停止 loading`)}>檢查 App 失敗終態</button>
      <button onClick={() => {
        savedStatus.current = document.querySelector<HTMLElement>('.improvement-page > [role="status"]');
        setReport(savedStatus.current ? '已保存清單狀態區，可搜尋到最後一筆再輸入不存在的關鍵字' : 'FAIL: 清單狀態區尚未掛載');
      }}>保存清單狀態區</button>
      <button onClick={() => {
        const region = document.querySelector<HTMLElement>('.improvement-page > [role="status"]');
        const results = document.querySelector('.im-results');
        const emptyTitle = results?.querySelector('.im-empty:not([role]) h2')?.textContent ?? '';
        const ok = region && region === savedStatus.current && results?.getAttribute('aria-busy') === 'false'
          && region.getAttribute('aria-live') === 'polite' && region.getAttribute('aria-atomic') === 'true'
          && region.textContent === emptyTitle;
        setReport(`${ok ? 'PASS' : 'FAIL'}: 同一清單狀態區與完成後空結果訊息一致`);
      }}>檢查清單狀態區</button>
      <output>{report}</output>
    </aside>
  </>;
}
createRoot(document.getElementById('root')!).render(new URLSearchParams(location.search).has('app') ? <AppFixture /> : <Fixture />);
