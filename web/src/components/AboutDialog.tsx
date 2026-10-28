import Modal from './Modal';
import type { VillageAges } from '../age';
import { snapshotPeriodText, villagePeriod } from '../regional';
import type { Coverage, Grid, NeedGrid, SupplementalData } from '../types';

function sourceLinks(value: unknown): string[] {
  const result = new Set<string>();
  const visit = (item: unknown) => {
    if (Array.isArray(item)) { item.forEach(visit); return; }
    if (!item || typeof item !== 'object') return;
    for (const [key, child] of Object.entries(item)) {
      if ((key === 'source_page' || key === 'page_url') && typeof child === 'string' && /^https?:\/\//.test(child)) result.add(child);
      else if (typeof child === 'object') visit(child);
    }
  };
  visit(value);
  return [...result];
}

export default function AboutDialog({ grid, coverage, need, ages, supplemental, onClose, onReplay }: { grid: Grid | null; coverage: Coverage | null; need: NeedGrid | null; ages: VillageAges | null; supplemental: SupplementalData; onClose: () => void; onReplay: () => void }) {
  const m = grid?.metadata;
  return (
    <Modal className="about" labelledBy="about-title" onClose={onClose} dismissOnBackdrop>{(dismiss) => <>
      <button className="close" aria-label="關閉" onClick={() => dismiss()}>×</button>
      <h2 id="about-title">資料與方法</h2>
      <section className="about-tour" aria-labelledby="about-tour-title">
        <h3 id="about-tour-title">快速導覽</h3>
        <p>查閱資料來源、計算假設，或重新認識主要功能。</p>
        <button className="btn-outline" onClick={onReplay}>重新觀看快速導覽</button>
        <small>重看導覽會保留目前地圖與設定。</small>
      </section>
      <h3>跨市資料快照</h3>
      <p>新北／跳蛙公車、里人口年齡、道路速率與推估旅時各自保留來源與期間。探索頁班次網格使用北北基官方 TDX 發布班表；站點覆蓋、需求落差及路線方案仍使用原臺北市資料模型。網站沒有自動輪詢即時資料。</p>
      {supplemental.supplemental_manifest?.records.map((record) => <details className="about-snapshot" key={record.id}>
        <summary>{record.title} · {record.feature_count.toLocaleString()} 筆</summary>
        <p>{snapshotPeriodText(record)}。{record.period !== null && <>來源更新時間：{record.source_updated_at ?? '未提供'}。</>}</p>
        <ul>{record.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
        <p>{sourceLinks(record.sources).map((url, index) => <a key={url} href={url} target="_blank" rel="noreferrer">官方來源 {index + 1}{' '}</a>)}</p>
      </details>) ?? <p>來源目錄載入中…</p>}
      {grid && m && <p>本頁呈現{m.scope_city_codes ? '北北基' : '臺北市'}公車路網、捷運路網、重要地點，以及以約 {grid.cell_m} m 方格彙整的公車計畫班次。重點是看出「哪裡、什麼時段有多少班公車經過」，不是實際載客量。</p>}
      <h3>來源</h3>
      <ul>
        <li>公車路線、站牌、班表：{m?.source ?? '臺北市公共運輸處／TDX 官方發布資料'}，來源時間 {m?.source_data_time ?? '未提供（各 TDX 端點時間不同）'}</li>
        <li>行政區界：內政部國土測繪中心（map-data/admin）</li>
        <li>捷運路線與車站、主要道路、重要地點：© OpenStreetMap contributors（Geofabrik 快照，map-data/osm、map-data/traffic-focus）</li>
        <li>社會住宅：臺北市都市發展局社宅 API（map-data/traffic-focus）</li>
        <li>YouBike 2.0 站點：臺北市政府交通局（data.taipei），只有站點與總停車格數</li>
        <li>站點周邊人口：內政部最小統計區戶籍人口（2024-12）與統計區界（2015 年版）</li>
        <li>底圖與路名：OpenFreeMap（OpenMapTiles／OpenStreetMap）</li>
      </ul>
      {grid && m ? <>
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
      </> : <p>切換至公車班次模式後，可查閱該網格的來源與計算摘要。</p>}
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
        <li>戶籍人口不是白天活動人口或搭乘量；相鄰站點範圍重疊，各站人口不可加總。最小統計區人口卡與村里年齡估算來源、月份不同，不能直接比較總數；缺值不以 0 代替。</li>
        <li>最小統計區卡的 350 m 年齡由五歲年齡組按面積分攤為四組；站點卡片另列所在里的 {villagePeriod(supplemental) ?? ''} 原始人口與三個年齡組；該「所在里」段不換算成站圈，下方村里年齡分攤另以獨立來源與方法估算。</li>
      </ul>
      <h3>村里年齡與站圈分攤</h3>
      <ul>
        <li>人口來源為內政部戶政司「村里戶數、單一年齡人口」，清冊與里界另依官方完整代碼、父行政區及名稱核對；全國核對不代表全國地圖已收錄。地圖僅顯示北北基既有範圍。</li>
        <li>村里圖層呈現所選年齡層占整個村里戶籍總人口的比例；三段與細分分組分開顯示。未匹配、缺值或總人口為 0 時比例為未知，顯示灰色。</li>
        <li>站圈以 TWD97 公尺投影計算村里與直線圓的相交面積，假設村里各年齡人口均勻分布。各年齡組缺值及涵蓋率分開保留；部分站圈只顯已知部分人數，不顯完整站圈比例。無里界部分也不是 0 人。</li>
        <li>站點資訊使用固定 350 m，資料另保留 200／350／500 m；站圈重疊，人口不可跨站加總。戶籍人口背景與面積估算都不等於實際乘車需求，不加入既有需求模型。</li>
        {ages && <li>人口 {ages.metadata.population_period}；清冊 {ages.metadata.roster_period}；里界 {ages.metadata.boundary_vintage}。{!ages.metadata.same_month_geometry_verified && '里界與人口月份不同，尚未證明同月幾何一致。'}</li>}
        {ages?.metadata.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}
        <li>資料來源：內政部戶政司、內政部國土測繪中心；政府資料開放授權條款第 1 版。</li>
      </ul>
      <h3>需求落差怎麼算（研究草案）</h3>
      <ul>
        <li>約 250 m 方格；人口 ≥ 100 人或附近有主要地點的格子才排名，其餘多為山區、河川、公園。</li>
        <li>需求＝居住人口、重要地點（學校、市場、市政機關、醫院、商場、科技園區、已完工社宅，依時段乘上時間係數並隨距離遞減）、附近捷運進出站人次三項排名的加權和，預設 50／30／20，可在面板調整。</li>
        <li>供給＝附近公車附線每小時計畫班次（隨距離遞減，再取飽和函數）與最近捷運站距離合成。</li>
        <li>落差＝需求排名 − 供給排名。九宮格各依排名分成三等分；四類摘要另以 0.5 為界，分為補強候選、供需皆高、供給偏高、供需皆低，不能據此判定服務飽和或過量。深框是權重各自 ±20% 抽樣 1,000 次仍有八成落在優先前 5% 的格子。</li>
        <li>捷運進出量來自 {need ? `${need.metadata.metro_period.start}～${need.metadata.metro_period.end}` : '最近一年'}，平日依行政機關辦公日曆排除國定假日，並剔除颱風等異常日。方法細節見 repo 的 docs/12_運輸需求權重方法.md。</li>
        {need?.metadata.limitations.map((x) => <li key={x}>{x}</li>)}
      </ul>
      {m && <p className="muted">班次網格建置：{m.built_at}</p>}
    </>}</Modal>
  );
}
