# Bus Analysis

使用臺灣開放資料建立協助評估公車路網的 GIS 系統，第一階段以臺北市為主，逐步納入人口、公車路線、站牌、班次與營運統計。

## 資料分類

- `raw_data/`：原始下載檔、解壓檔、PDF 文字／OCR 與處理中的資料，按來源分目錄。
- `data/`：整理後的 UTF-8 JSON／GeoJSON，附來源、期間、單位與必要限制。來源本身是 JSON 也要先經整理。
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

## 更新

安裝、執行與自動更新方式見 [資料更新說明](docs/data-updates.md)。
