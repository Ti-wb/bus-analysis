# Bus Analysis

使用臺灣開放資料建立協助評估公車路網的 GIS 系統，第一階段以臺北市為主，逐步納入人口、公車路線、站牌、班次與營運統計。

## 資料分類

- `raw_data/`：原始下載檔、解壓檔、PDF 文字／OCR 與處理中的資料，按來源分目錄。
- `data/`：整理後的 UTF-8 JSON／GeoJSON，附來源、期間、單位與必要限制。來源本身是 JSON 也要先經整理。
- `map-data/`：雙北 OSM 地圖圖層與官方市、區、里界；[圖層目錄](map-data/catalog.json)、[讀取與更新方式](map-data/README.md)。大型原始檔不進 Git。
- 缺值保留為 `null`；GeoJSON 使用 WGS84 `[經度, 緯度]`。即時到站預估、預定班表、實際班次與段次分開處理。

Agent 協作、格式與提交規則見 [AGENTS.md](AGENTS.md)。

## 本次來源與輸出

| 來源 | 整理後資料 |
| --- | --- |
| [臺北市公車路線軌跡](https://data.taipei/dataset/detail?id=efad90af-4bbe-4c6a-ac60-06f3e52d8a97) | `data/bus_routes.geojson` |
| [臺北市公車站牌位置圖](https://data.taipei/dataset/detail?id=48aa5bca-2a4f-4fb7-a658-43cba51d5d56) | `data/bus_stops.geojson` |
| [臺北市預估到站時間](https://data.taipei/dataset/detail?id=f11a5af0-7b37-48ef-98cc-f6f102ed43c6) | `data/bus_arrivals.json`，僅最近一次快照 |
| [公共運輸處統計附件](https://pto.gov.taipei/RelData.aspx?ParentSN=5D189624CC86562D&sms=2A073ABACACC5A2A) | `data/pto_tables.json`、`data/bus_route_statistics.json`、`data/bus_operator_statistics.json` |

公車資料的範圍、來源時間與品質限制見 [資料目錄](data/taipei_bus_catalog.json) 及各檔案的 `metadata`。

新增的現行公車、近三年人口與營運統計、捷運一年分時進出量、設施及 TDX 資料，見[整合資料目錄](data/catalog.json)與[整理及更新說明](docs/data-collection.md)。大型資料僅保留必要彙總或來源連結；各來源均另附獨立巡檢結果。

臺北市學校、醫院、住宅區等[交通需求候選清單](docs/traffic-focus.md)提供 CSV、GIS 標記與範圍，另含官方社宅規劃戶數及工程狀態；設施位置不代表實測高流量。

## 前端地圖

`web/` 是公車與捷運路網、公車班次網格、站點覆蓋與站牌服務範圍的單頁地圖，線上版在 <https://bus.tw.pe/>；產生資料與啟動方式見 [前端地圖說明](docs/web-map.md)。

## 更新

安裝、執行與自動更新方式見 [資料更新說明](docs/data-updates.md)。專案用 uv 管理 Python 與依賴，不熟悉的話請先看 [uv 快速上手](docs/uv.md)。Python、前端與 GitHub Actions 的依賴由 Renovate 每週開 PR 更新，設定在 `.github/renovate.json5`。

## 研究文件（MVP 預研究）

研究課題：臺北市公車路網擁擠與重疊的可視化解決方案。用站點上下車資料找熱區與冷區、衡量公車與捷運的重疊程度、評估 DRTS 或小型巴士的導入區塊，並把駕駛長的休息需求納入考量。逐站運量目前使用 mock 資料，原因見 01。

| # | 文件 | 內容 |
| --- | --- | --- |
| 00 | [研究計畫總覽](docs/00_研究計畫總覽.md) | 背景、目的、研究問題、主要發現、風險 |
| 01 | [資料來源目錄](docs/01_資料來源目錄.md) | 公運處 feeds、TDX（含票證服務）、捷運、統計、底圖；附驗證狀態 |
| 02 | [已驗證資料探勘](docs/02_已驗證資料探勘.md) | 連線結果、欄位、站位堆疊與捷運鄰近度的初步數據 |
| 03 | [背景與政策現況](docs/03_背景與政策現況.md) | 路網規模、重疊相關法規、跳蛙公車、DRTS、國際案例 |
| 04 | [分析方法](docs/04_分析方法.md) | 熱區與冷區、三種重疊度、接駁圈、DRTS 適用性 |
| 05 | [駕駛長過勞與折返點](docs/05_駕駛長過勞與折返點.md) | 工時法規、缺工、折返點與休息排班 |
| 06 | [MVP 設計與技術選型](docs/06_MVP設計與技術選型.md) | 介面、技術、管線、里程碑 |
| 07 | [Mock 資料規格](docs/07_Mock資料規格.md) | mock 運量模型、校準方式與限制 |
| 08 | [問題與待辦清單](docs/08_問題與待辦清單.md) | 所有遇到的問題與未解事項 |
| 09 | [參考文獻與連結索引](docs/09_參考文獻與連結索引.md) | 法規、平台、新聞、論文、工具 |
| 10 | [遠端資料整併與新發想](docs/10_遠端資料整併與新發想.md) | 資料管線與研究的整併、新發想 |
| 11 | [時間地點方向需求熱圖設計](docs/11_時間地點方向需求熱圖設計.md) | 時間 × 地點 × 方向的需求圖層 |

研究用腳本的用法見 [研究腳本說明](docs/research-scripts.md)。
