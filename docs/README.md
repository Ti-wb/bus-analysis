# 文件索引

[回專案首頁](../README.md)。目前產品範圍以 [User Story](product-stories.md) 與操作文件為準；早期研究中的建議功能、模型與時程不代表已實作。

## 產品與開發

| 文件 | 用途 |
| --- | --- |
| [產品主線與 User Story](product-stories.md) | 故事 ID、完成條件、Penpot 04／04-01／07 設計對照 |
| [前端地圖](web-map.md) | 啟動、建置、部署、圖層、站點資訊與資料限制 |
| [改善優先清單](improvement-list.md) | 四類清單、篩選排序、CSV、地圖／路線編輯銜接與 Mock 邊界 |
| [路線方案工具](route-editor.md) | 新增／編輯／刪除、影響比較、本機保存及驗證 |
| [品牌指南](../branding/BCS-Brand-Guidelines.pdf) | Bus coming soon 識別與介面規範 |

## 資料與維護

| 文件 | 用途 |
| --- | --- |
| [資料整理](data-collection.md) · [公車 feeds](taipei-bus-feeds.md) | 現行公車、人口、捷運與設施等來源、輸出及更新腳本 |
| [資料更新](data-updates.md) · [uv 入門](uv.md) | Python 環境、公車基本資料與統計更新、自動更新 |
| [GIS 圖層](../map-data/README.md) · [地圖更新](map-updates.md) | 圖層範圍、來源、讀取與更新方式 |
| [跨市資料](official-cross-city.md) · [TDX](tdx.md) | 跨市 ID、人口里界、服務日曆、旅時及來源限制 |
| [需求落差](transport-need.md) · [交通需求候選](traffic-focus.md) | 需求指標、設施候選與衍生資料用法 |
| [Agent 協作規則](../AGENTS.md) | 來源分工、資料格式、獨立巡檢及提交規則 |

## 資料來源與輸出

以下為公車基本輸出；前端現行公車來源優先使用 `data/taipei_feeds/`，詳見[資料整理](data-collection.md)。
| 來源 | 整理後資料 |
| --- | --- |
| [臺北市公車路線軌跡](https://data.taipei/dataset/detail?id=efad90af-4bbe-4c6a-ac60-06f3e52d8a97) | `data/bus_routes.geojson` |
| [臺北市公車站牌位置圖](https://data.taipei/dataset/detail?id=48aa5bca-2a4f-4fb7-a658-43cba51d5d56) | `data/bus_stops.geojson` |
| [臺北市預估到站時間](https://data.taipei/dataset/detail?id=f11a5af0-7b37-48ef-98cc-f6f102ed43c6) | `data/bus_arrivals.json`，僅最近一次快照 |
| [公共運輸處統計附件](https://pto.gov.taipei/RelData.aspx?ParentSN=5D189624CC86562D&sms=2A073ABACACC5A2A) | `data/pto_tables.json`、`data/bus_route_statistics.json`、`data/bus_operator_statistics.json` |

公車資料的範圍、來源時間與品質限制見 [資料目錄](../data/taipei_bus_catalog.json) 及各檔案的 `metadata`。

現行公車、近三年人口與營運統計、捷運一年分時進出量、設施及 TDX 資料，見[整合資料目錄](../data/catalog.json)與[整理及更新說明](data-collection.md)。大型資料僅保留必要彙總或來源連結；各來源均另附獨立巡檢結果。

跨市 ID 與站序、北北基人口里界版本、區域服務日曆、步行轉乘證據、TDX 站間旅時與道路速率／流量，見
[補充資料目錄](../data/official_cross_city/validation_catalog.json)及[跨市資料用法](official-cross-city.md)。
完整性與尚待來源釐清的項目見 [Validation 狀態](../data/quality/pr_validation.json)。

臺北市學校、醫院、住宅區等[交通需求候選清單](traffic-focus.md)提供 CSV、GIS 標記與範圍，另含官方社宅規劃戶數及工程狀態；設施位置不代表實測高流量。

## 研究文件

以下保留 MVP 預研究與方法草案，涵蓋路網重疊、站點熱冷區、DRTS／小型巴士及駕駛休息；其中建議功能與時程不等於目前已實作範圍。逐站運量研究使用 mock 資料，原因見 01。

| # | 文件 | 內容 |
| --- | --- | --- |
| 00 | [研究計畫總覽](00_研究計畫總覽.md) | 背景、目的、研究問題、主要發現、風險 |
| 01 | [資料來源目錄](01_資料來源目錄.md) | 公運處 feeds、TDX（含票證服務）、捷運、統計、底圖；附驗證狀態 |
| 02 | [已驗證資料探勘](02_已驗證資料探勘.md) | 連線結果、欄位、站位堆疊與捷運鄰近度的初步數據 |
| 03 | [背景與政策現況](03_背景與政策現況.md) | 路網規模、重疊相關法規、跳蛙公車、DRTS、國際案例 |
| 04 | [分析方法](04_分析方法.md) | 熱區與冷區、三種重疊度、接駁圈、DRTS 適用性 |
| 05 | [駕駛長過勞與折返點](05_駕駛長過勞與折返點.md) | 工時法規、缺工、折返點與休息排班 |
| 06 | [MVP 設計與技術選型](06_MVP設計與技術選型.md) | 介面、技術、管線、里程碑 |
| 07 | [Mock 資料規格](07_Mock資料規格.md) | mock 運量模型、校準方式與限制 |
| 08 | [問題與待辦清單](08_問題與待辦清單.md) | 所有遇到的問題與未解事項 |
| 09 | [參考文獻與連結索引](09_參考文獻與連結索引.md) | 法規、平台、新聞、論文、工具 |
| 10 | [遠端資料整併與新發想](10_遠端資料整併與新發想.md) | 資料管線與研究的整併、新發想 |
| 11 | [時間地點方向需求熱圖設計](11_時間地點方向需求熱圖設計.md) | 時間 × 地點 × 方向的需求圖層 |
| 12 | [運輸需求權重方法](12_運輸需求權重方法.md) | 人口、地點、班次、時間與方向的供需落差指標 |

研究用腳本的用法見 [研究腳本說明](research-scripts.md)。
