# 臺北市公車直接介接資料

`scripts/update_bus_feeds.py` 取得清單中的 14 份公開 `.gz` feed，保留原始壓縮檔與官方 V6.3 欄位規格，再輸出 JSON／GeoJSON。研究範圍為臺北市；來源中跨市營運的路線與站牌保留，未額外抓取新北市 feed。

```bash
uv run scripts/update_bus_feeds.py
uv run scripts/update_bus_feeds.py --offline
```

離線重建會檢查壓縮檔、JSON／XML 格式，再檢查欄位、筆數、識別碼與座標；線上更新會替換本組的最新快照。`--download-only` 只下載，尚未完成資料整理。完整來源、下載時間與資料時間在 `raw_data/taipei_feeds/manifest.json`。整理結果及各表品質在 `data/taipei_feeds/`；`raw_data/taipei_feeds/delivery.json` 列出輸出與限制。

| 整理檔 | 粒度與用途 |
| --- | --- |
| `routes.json` | 每個主路線 ID 的名稱、業者與分支識別碼集合 |
| `route_branches.json` | 主路線 × 分支 × 業者；相同整列重複已折疊，保留所有 `source_row_indices` |
| `route_stop_sequences.json` | 每個分支的實際站序；以站牌 ID 精確連接方向及站位 |
| `stops.geojson` | 路線站牌；其筆數不是實體站位數 |
| `stop_locations.geojson` | 地理站位 |
| `route_shapes.geojson` | 主路線或分支 × 方向的 WGS84 線形；沒有運量 |
| `fixed_schedules.json` | 固定發車時間與日曆規則 |
| `headway_schedules.json` | 分時班距與日曆規則 |
| `special_schedules.json` | 特殊日期 × 班表項目 × 列出的停靠站；不是完整站站班表 |
| `vehicle_positions.geojson` | 即時車輛位置快照 |
| `stop_events.json` | 即時到離站事件快照 |
| `occupancy_events.json` | 車上人數與擁擠等級快照 |
| `estimated_arrivals.json` | 預估到站秒數與服務狀態 |
| `vehicles.json` | 車型與設備；沒有核定載客容量 |
| `subroute_depots.json` | 分支與營業站 ID 關係；沒有場站位置或容量 |

識別碼依官方欄位定義分開：位置、事件與擁擠度來源的 `RouteID` 是附屬路線，輸出為 `subroute_id`；ETA 與站牌來源的 `RouteID/routeId` 是主路線，輸出為 `route_id`。事件的 `StationID` 是車輛所屬場站，輸出為 `depot_id`。禁止只看來源欄名相同就直接關聯。

來源更新時間和逐筆事件時間保留為含時區 ISO 8601；下載時間另列。動態資料只是一個快照，不能代表近三年的服務可靠度。固定與班距表是預定供給；特殊日期資料依來源列出的日期保留，不能用現行班表回填歷史。

`RemainingNum` 轉為 `onboard_people`，代表車上人數；未提供值維持 `null`。ETA 負值轉為服務狀態；`GoBack=2/3` 不是去返方向。線形 `SubRouteID=-1` 表示主路線線形，輸出 `shape_scope=main_route`、`subroute_id=null` 並保留原碼。未知代碼與無效方位角保留來源值及品質註記。

班距表 `LongHeadway` 與 `LowHeadway` 依原欄名輸出分鐘數；官方文件的高／低說明和多數實際數值大小不一致，因此不擅自改成最短／最長。跨 feed 未匹配的 ID 保留在品質報告，不能以名稱猜補。每份資料的 `metadata` 提供來源、欄位／單位、粒度、缺值規則與具體限制。
