import { useEffect, useRef } from 'react';
import type { Coverage, Grid, NeedGrid } from '../types';

export default function AboutDialog({ open, grid, coverage, need, onClose }: { open: boolean; grid: Grid | null; coverage: Coverage | null; need: NeedGrid | null; onClose: () => void }) {
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
            <li>YouBike 2.0 站點：臺北市政府交通局（data.taipei），只有站點與總停車格數</li>
            <li>站點周邊人口：內政部最小統計區戶籍人口（2024-12）與統計區界（2015 年版）</li>
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
          <h3>站點覆蓋怎麼算</h3>
          <ul>
            <li>約 50、100 或 200 m 方格（在「偏好」切換），只保留格心在臺北市內的格子；每格記錄格心到最近公車站位、捷運車站、YouBike 站的直線距離（TWD97 平面公尺）。</li>
            <li>公車、捷運、YouBike 各有自己的步行半徑，格子取最靠近站心的那一段；YouBike 騎乘只看 YouBike 站。半徑內依距離分 5 段，越近越深。</li>
            {coverage?.metadata.limitations.map((x) => <li key={x}>{x}</li>)}
          </ul>
          <h3>站點資訊怎麼算</h3>
          <ul>
            <li>點選公車、捷運或 YouBike 站點，以該站為圓心查看固定直線 350 m 內的戶籍人口、戶數與附近設施；不改變覆蓋圖層設定。</li>
            <li>統計區只有部分在圓內時依面積比例分攤，假設區內人口均勻分布；只有臺北市人口，新北市部分不計。</li>
            <li>戶籍人口不是白天活動人口或搭乘量；相鄰站點範圍重疊，各站人口不可加總。現有來源未提供年齡分布，缺值不以 0 代替。</li>
          </ul>
          <h3>需求落差怎麼算（研究草案）</h3>
          <ul>
            <li>約 250 m 方格；人口 ≥ 100 人或附近有主要地點的格子才排名，其餘多為山區、河川、公園。</li>
            <li>需求＝居住人口、重要地點（學校、市場、市政機關、醫院、商場、科技園區、已完工社宅，依時段乘上時間係數並隨距離遞減）、附近捷運進出站人次三項排名的加權和，預設 50／30／20，可在面板調整。</li>
            <li>供給＝附近公車附線每小時計畫班次（隨距離遞減，再取飽和函數）與最近捷運站距離合成。</li>
            <li>落差＝需求排名 − 供給排名；以 0.5 分成優先補強、已飽和、可能過量、需求與供給都低四類。深框是權重各自 ±20% 抽樣 1,000 次仍有八成落在優先前 5% 的格子。</li>
            <li>捷運進出量來自 {need ? `${need.metadata.metro_period.start}～${need.metadata.metro_period.end}` : '最近一年'}，平日依行政機關辦公日曆排除國定假日，並剔除颱風等異常日。方法細節見 repo 的 docs/12_運輸需求權重方法.md。</li>
            {need?.metadata.limitations.map((x) => <li key={x}>{x}</li>)}
          </ul>
          <p className="muted">資料建置：{m.built_at}</p>
        </>
      )}
    </dialog>
  );
}
