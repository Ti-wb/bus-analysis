import { TOUR_STEPS } from './content';

function Explore() {
  return <div className="tour-map">
    <svg viewBox="0 0 598 366" preserveAspectRatio="none" aria-hidden="true">
      <rect width="598" height="366" fill="#ebf2f5" />
      <rect x="42" y="48" width="86" height="64" rx="12" fill="#dce9df" />
      <path d="M70-10C145 80 5 126 95 195S153 268 76 360" fill="none" stroke="#c4e4ec" strokeWidth="54" />
      <g fill="#f3d9cb">
        {[0, 1, 2, 3, 4].flatMap((row) => [0, 1, 2, 3, 4].filter((column) => row > 0 && row < 4 || column > 0 && column < 4).map((column) =>
          <rect key={`${row}-${column}`} x={128 + column * 21} y={128 + row * 21} width="19" height="19" />))}
        {[0, 1, 2, 3, 4].flatMap((row) => [0, 1, 2].map((column) =>
          <rect key={`b-${row}-${column}`} x={389 + column * 21} y={238 + row * 21} width="19" height="19" />))}
        <rect x="248" y="149" width="19" height="61" />
      </g>
      <path d="M0 84H598M0 176H598M0 269H598M180 24V366M300 24V366M420 24V366" stroke="white" strokeWidth="12" />
      <path d="M0 84H598M0 176H598M0 269H598M180 24V366M300 24V366M420 24V366" stroke="#e1eaf3" strokeWidth="1" />
      <path d="M180 24V366" stroke="#9281b2" strokeWidth="4" />
      <path d="M24 180H420V290H574" fill="none" stroke="#1d559f" strokeWidth="5" />
      <path d="M180 186H290" stroke="#fd5f19" strokeWidth="2" strokeDasharray="4 4" />
      <circle cx="180" cy="180" r="9" fill="white" stroke="#fd5f19" strokeWidth="3" />
    </svg>
    <span className="tour-tag tour-map-modes">公車 · 捷運 · YouBike</span>
    <span className="tour-tag tour-map-time">平日 / 早尖峰</span>
    <span className="tour-map-area">生活圈 A</span>
    <div className="tour-station">
      <span className="tour-tag">公車站</span>
      <h3>從站點，認識周邊</h3>
      <p>350 m 直線範圍</p>
      <p>人口與年齡分布<br />學校、醫療與重要設施</p>
    </div>
    <span className="tour-map-preferences">到「偏好」切換分析圖層</span>
  </div>;
}

function Improvements() {
  const rows = [
    ['路線 A', '服務重疊', '先核對服務範圍'],
    ['路線 B', '時段落差', '查看班次與需求指標'],
    ['路線 C', '端點設施', '關注駕駛休息條件'],
  ];
  return <div className="tour-demo tour-list-demo">
    <div className="tour-demo-heading"><h3>改善優先清單</h3><span className="tour-tag tour-mock">部分為 Mock</span></div>
    <div className="tour-filters"><span className="tour-tag">平日早峰</span><span className="tour-tag">所有行政區</span><span className="tour-tag">問題類型</span></div>
    <div className="tour-table-head"><span>候選路線</span><span>要釐清的問題</span></div>
    <div className="tour-table">{rows.map(([route, issue, detail]) => <div className="tour-table-row" key={route}>
      <b>{route}</b><div><b>{issue}</b><small>{detail}</small></div><span aria-hidden="true">›</span>
    </div>)}</div>
    <p className="tour-demo-flow tour-demo-flow-dark">展開詳情 → 在地圖上檢視 → 加入路線編輯方案</p>
  </div>;
}

function Routes() {
  return <div className="tour-demo tour-route-demo">
    <div className="tour-demo-heading"><h3>路線方案 / 影響預覽</h3><span className="tour-tag">假設試算</span></div>
    <div className="tour-comparison">
      <div className="tour-route-sketch">
        <b>調整站序與停靠</b>
        <svg viewBox="0 0 238 160" aria-label="原始路徑由 A 站經 B 站向南，方案由 B 站斜接新增站" role="img">
          <path d="M28 40H108V110H208" fill="none" stroke="#a7bed7" strokeWidth="5" />
          <path d="M28 40H108L208 110" fill="none" stroke="#fd5f19" strokeWidth="5" />
          <g fill="white" stroke="#1d559f" strokeWidth="3"><circle cx="28" cy="40" r="8" /><circle cx="108" cy="40" r="8" /></g>
          <circle cx="208" cy="110" r="8" fill="white" stroke="#fd5f19" strokeWidth="3" />
          <g fill="currentColor" fontSize="12"><text x="13" y="74">A 站</text><text x="91" y="19">B 站</text><text x="177" y="146" fill="#9d3d0b">新增站</text></g>
        </svg>
        <div className="tour-route-key"><span>原始</span><span>方案</span></div>
      </div>
      <div className="tour-impacts"><b>分開查看，不只看淨增加</b><span>新增服務</span><span>失去服務</span><span>班次減少</span><small>高齡／學生影響：未估算</small></div>
    </div>
    <p className="tour-demo-flow">編輯草稿 → 預覽影響 → 套用到本機 ↶ 可復原</p>
  </div>;
}

function Share() {
  return <div className="tour-demo tour-share-demo">
    <div className="tour-people">{[['市', '市府'], ['業', '客運業者'], ['民', '居民團體'], ['駕', '駕駛代表']].map(([symbol, label]) =>
      <div key={symbol}><b>{symbol}</b><span>{label}</span></div>)}</div>
    <div className="tour-shared-view">
      <h3>同一個視圖，相同的比較條件</h3>
      <p>地圖位置 · 日型／時段 · 篩選設定</p>
      <div className="tour-share-actions"><span>分享視圖</span><span>清單 CSV</span><span>資料與方法</span></div>
      <small>保留來源與限制，讓討論可追溯。</small>
    </div>
  </div>;
}

export default function Illustration({ step }: { step: number }) {
  return <figure className="tour-illustration" aria-label={`${TOUR_STEPS[step].label}功能示意`}>
    <figcaption>功能示意 · 使用相同介面理解操作</figcaption>
    {[<Explore key="explore" />, <Improvements key="improvements" />, <Routes key="routes" />, <Share key="share" />][step]}
  </figure>;
}
