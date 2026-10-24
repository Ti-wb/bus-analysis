# 前端地圖（覆蓋臺北）

`web/` 是單頁地圖，呈現臺北市及周邊的公車路線、捷運路網，以及公車計畫班次網格。用 React、TypeScript、react-map-gl（MapLibre GL JS）與 Vite 建置，輸出為靜態網站，不需要地圖金鑰或後端；底圖與路名使用 OpenFreeMap 公開圖磚，需連網。

## 執行

```bash
# 1. 由 repo 內資料產生前端圖層，輸出到 web/public/data/（不進 Git）
uv run scripts/build_web_data.py
# 2. 安裝前端套件並啟動開發伺服器（http://localhost:5173）
npm --prefix web install
npm --prefix web run dev
# 3. 型別檢查並建置靜態檔到 web/dist/
npm --prefix web run build
```

資料更新後重跑第 1 步即可。只檢查型別可用 `npm --prefix web run typecheck`。

## 程式結構

| 位置 | 內容 |
| --- | --- |
| `web/src/App.tsx` | 狀態（日型、小時、網格大小、圖層、地點類別）、資料載入、網址同步 |
| `web/src/map/` | 地圖、圖層樣式、滑鼠提示與彈出視窗 |
| `web/src/components/` | 頂列、左側面板、搜尋、資料與方法說明 |
| `web/src/config.ts` | 色階、類別、圖層分組等設定 |
| `web/src/data.ts`、`hashState.ts` | 資料讀取與網格計算、網址狀態 |

資料內容一律以 JSX 輸出，不直接寫入 HTML 字串。

## 圖層與來源

| 圖層 | 來源 | 處理 |
| --- | --- | --- |
| 底圖與路名 | [OpenFreeMap](https://openfreemap.org/) Positron 向量圖磚（OpenMapTiles／OSM） | 底圖在最下層；路名與地名改用中文名稱，疊在網格與路線之上 |
| 行政區界、主要道路 | `map-data/admin`、`map-data/osm` | 裁到臺北市周邊範圍並簡化線形；道路以白線疊在網格上 |
| 公車路線、站牌 | `data/taipei_feeds` 的 `route_shapes`、`stop_locations`、`route_stop_sequences` | 線形簡化；站牌附路線清單與平日計畫發車數 |
| 捷運路網、車站 | `map-data/osm` 的 `railways`、`transport` | 只取具名的捷運與輕軌線，排除機廠、測試軌；車站取路線約 80 m 內的軌道車站 |
| 重要地點 | `map-data/traffic-focus` 的 OSM 設施候選與臺北市社宅 | 依類別上色、可逐類開關；住宅區、商業產業等具名土地使用候選預設隱藏；社宅點大小依規劃戶數。位置是候選標記，不是出入口，也不代表人流 |
| YouBike 站點 | `data/youbike/stations.geojson`（臺北市交通局 YouBike 2.0，見[資料整理](data-collection.md)） | 預設關閉，放大到 12.5 級後顯示；點大小依總停車格數，快照時暫停營運的站點為灰色。只有站點，沒有即時車數 |
| 公車班次網格 | `fixed_schedules`、`headway_schedules` 加上站序 | 見下節 |

## 班次網格

- 約 250 m 與 500 m 兩種經緯度方格（以北緯 25.05° 換算），只保留與臺北市界相交的格子。
- 每格的值是格內站牌所屬附線，在該小時的計畫發車數加總；同一附線在同一格只算一次，不分方向。
- 固定班表優先；同一小時沒有固定班表時，用班距區間換算（最長、最短班距平均）。
- 平日以週三班表代表，週六、週日分開計算；「全日」是 24 小時加總。
- 格子狀態：無站牌、有班表、部分附線缺班表（數值偏低，滑鼠移上去會提示）、全部附線缺班表（斜紋，不是 0）。

## 限制

- 以起站發車時刻歸入小時，沒有加上行駛到該格的時間。
- 只有臺北市公共運輸處 feed，新北市專屬路線不在內，市界附近會低估。
- 這是計畫供給，不是實際發車或載客量；也不代表步行可達性。
- 捷運沒有班次資料，只呈現路網；捷運站與公車站牌沒有做 ID 對應。

## 部署（GitHub Pages）

`.github/workflows/deploy-web.yml` 在 `main` 的前端程式或來源資料變更時，產生地圖資料、型別檢查並建置，再發布到 GitHub Pages；也可在 Actions 頁面手動執行。相關 PR 只建置不發布，用來提早發現錯誤。

網址為 <https://bus.tw.pe/>。Pages 來源（GitHub Actions）與自訂網域都在 **Settings → Pages** 設定，需 repo 管理員權限；DNS 以 CNAME 指向 `ti-wb.github.io`。建置使用相對路徑，換網域不用改程式。
