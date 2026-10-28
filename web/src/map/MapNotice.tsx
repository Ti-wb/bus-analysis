import { useEffect, useState } from 'react';
import type { Basemap, MapLoadState } from './loadState';

export default function MapNotice({ state, onRetry, onDismiss, retained = '目前選取與路線草稿仍保留。' }: {
  state: MapLoadState; onRetry: (base: Basemap) => void; onDismiss: () => void; retained?: string;
}) {
  const [open, setOpen] = useState(true);
  useEffect(() => { setOpen(true); }, [state.attempt, state.issue]);
  const local = state.base === 'local', ready = state.status === 'ready';
  const message = state.status === 'failed'
    ? `${state.issue === 'context' ? '地圖繪圖連線已中斷' : '地圖無法完成載入'}。請重試地圖；${retained}`
    : state.issue === 'resource'
      ? local ? '部分資料圖層載入失敗；已載入內容仍可操作，請重試地圖。'
        : '部分底圖或資料圖層載入失敗；已載入內容仍可操作。可重試地圖或使用本地簡圖。'
      : local ? ready ? '已使用本地簡圖；道路、行政界與資料圖層可繼續操作，沒有線上底圖路名。' : '正在載入本地簡圖…'
        : null;
  return <>
    {message && (open || !ready) && <div className="map-notice" role={state.status === 'failed' ? 'alert' : 'status'}>
      <span>{message}</span>
      {state.status !== 'loading' && <button onClick={() => onRetry(state.base)}>重試地圖</button>}
      {!local && <button onClick={() => onRetry('local')}>使用本地簡圖</button>}
      {local && <button onClick={() => onRetry('online')}>重試線上底圖</button>}
      {ready && <button className="notice-close" aria-label="關閉底圖提示" onClick={() => { setOpen(false); if (!local) onDismiss(); }}>×</button>}
    </div>}
    {local && !open && ready && <button className="map-base-options" onClick={() => setOpen(true)}>本地簡圖・底圖選項</button>}
  </>;
}
