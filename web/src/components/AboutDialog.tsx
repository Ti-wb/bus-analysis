import { useEffect, useRef } from 'react';
import type { Grid } from '../types';

export default function AboutDialog({ open, grid, onClose }: { open: boolean; grid: Grid | null; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const m = grid?.metadata;
  return (
    <dialog ref={ref} className="about" onClose={onClose}>
      <button className="close" aria-label="關閉" onClick={onClose}>×</button>
      <h2>資料與方法</h2>
      {!grid || !m ? <p>載入中…</p> : (
        <>
          <p>本頁呈現臺北市公車路網、捷運路網、重要地點，以及以約 {grid.cell_m} m 方格彙整的公車計畫班次。重點是看出「哪裡、什麼時段有多少班公車經過」，不是實際載客量。</p>
          <h3>來源</h3>
          <ul>
            <li>公車路線、站牌、班表：{m.source}，來源時間 {m.source_data_time}</li>
            <li>行政區界：內政部國土測繪中心（map-data/admin）</li>
            <li>捷運路線與車站、主要道路、重要地點：© OpenStreetMap contributors（Geofabrik 快照，map-data/osm、map-data/traffic-focus）</li>
            <li>社會住宅：臺北市都市發展局社宅 API（map-data/traffic-focus）</li>
            <li>底圖與路名：OpenFreeMap（OpenMapTiles／OpenStreetMap）</li>
          </ul>
          <h3>網格怎麼算</h3>
          <ul>
            <li>{m.cell}。</li>
            <li>{m.value}。</li>
            <li>平日以週三班表代表，週六、週日分開計算。</li>
          </ul>
          <h3>限制</h3>
          <ul>{m.limitations.map((x) => <li key={x}>{x}</li>)}</ul>
          <h3>網格統計</h3>
          <p>
            共 {grid.cells.length} 格：有班表 {m.status_counts.ok + m.status_counts.partial} 格（其中 {m.status_counts.partial} 格有部分附線缺班表）、
            無站牌 {m.status_counts.no_stop} 格、資料缺漏 {m.status_counts.no_schedule} 格。
            有班表的附線 {m.scheduled_subroutes} 條／有站序的附線 {m.sequence_subroutes} 條。
          </p>
          <p className="muted">資料建置：{m.built_at}</p>
        </>
      )}
    </dialog>
  );
}
