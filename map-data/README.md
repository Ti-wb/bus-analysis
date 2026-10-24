# 雙北 GIS 地圖資料

[圖層目錄](catalog.json) 提供檔案路徑、筆數、大小、版本、範圍與獨立巡檢結果。路徑相對本目錄；全部為 WGS84 `[經度, 緯度]`，可供 MapLibre、Leaflet 或 GIS 軟體使用。

| 目錄 | 內容與版本 |
| --- | --- |
| `admin/` | 國土測繪中心：2 市、41 區、1,512 里；市／區界 2025-03-18，里界 2026-08-17。 |
| `admin/admin_index.json` | 官方代碼、父層代碼、名稱、bbox 與面內標籤位置；共 1,555 列。 |
| `osm/` | Geofabrik OSM 2026-10-20 快照；道路及步行路徑、軌道、水系、綠地、建物、地名、交通與公共設施等 19 圖層。 |
| `traffic-focus/` | 臺北市交通需求候選設施、具名住宅／產業區及官方社宅工程；[清單、資料來源與用法](../docs/traffic-focus.md)。 |
| `population/moi_village_age_2026-09.geojson.gz` | 北北基 1,677 個官方完整里界與 2026-09 逐歲人口、8 個分組及明確核對狀態；主里界 2026-08-17，未宣稱同月幾何。 |
| `quality/` | 不同於整理者的 Agent 所作獨立巡檢。 |

跨市驗收另提供 `admin/keelung_villages.geojson`：2026-08-17 官方版本，157 個有對應人口的里及
8 個未編定村里區塊，合計 165 個圖徵。來源與 CRS 轉換記錄於檔案 metadata，
獨立巡檢見 [`quality/keelung_review.json`](quality/keelung_review.json)；人口版本對照用法見
[跨市資料說明](../docs/official-cross-city.md)。此補充層不改動原雙北圖層範圍。

村里年齡另以戶政司 11509 全國獨立清冊核對全部 7,781 個人口村里。NLSC ZIP 主層 7,986 筆加上日期不明的 `Village_Sanhe` 附層 1 筆，合計 7,987 個圖形；206 個未編定區塊無對應人口，維持 null。17 村名稱差異未核實，Sanhe 與六個主層村里重疊，這 24 村合計 45,064 人排除分攤、保留來源數值。全國核對表存於 `data/population/`；提交的年齡 GeoJSON 僅涵蓋北北基，沒有把遠島裁掉後宣稱全國完整。

年齡 GeoJSON 使用 EPSG:4326，保留所有原始座標；`web/public/data/village_age.geojson` 才是顯示簡化副本。`scripts/build_age_catchments.py` 以完整里界轉 EPSG:3826 計算北北基站圈，未核對與缺值均不補零，人口不代表搭乘需求。抓取、離線重建、欄位及年齡分攤規則見[村里年齡與站圈](../docs/moi-village-age-geography.md)，全量圖形／歲別獨立巡檢見 [`quality/village_age_geography_review.json`](quality/village_age_geography_review.json)。

OSM 只保留與雙北官方市界相交的圖徵，線與面裁切至市界。行政界未簡化；OSM 線形未簡化，座標取 7 位小數，造成無效幾何時保留完整精度。原始下載及中間檔僅使用 `raw/` 作為暫存，排除 Git；來源連結、實際版本與清理方式記錄於各 `manifest.json`。

OSM 使用無損壓縮的 `.geojson.gz`；大型圖層切成多個檔案，**同圖層所有分片合起來才是完整資料**。可先解壓後交給 GIS 軟體，或用附帶的瀏覽器工具讀取：

```js
import { loadGeoJSON } from './map-data/load-geojson.mjs';
const catalog = await fetch('./map-data/catalog.json').then(r => r.json());
const roads = catalog.records.find(r => r.dataset_id === 'osm_taipei_new_taipei_roads');
const collections = await Promise.all(
  roads.files.map(file => loadGeoJSON(`./map-data/${file.path}`))
);
// 將各 FeatureCollection 加入地圖；依目前視野載入需要的其他圖層。
```

更新與離線重建方式見 [地圖資料更新說明](../docs/map-updates.md)。新下載或來源變更後須重新巡檢，既有巡檢只對本次版本負責。

使用限制：

- 官方行政代碼適合關聯，名稱不可當唯一鍵；里人口或其他統計資料需先核對版本與代碼。標籤點是面內代表點，不是公所位置或人口重心。
- OSM 免費 GIS 匯出是圖徵與欄位選集，缺少完整轉向限制、通行權限、車高車重、建物高度等資訊；適合地圖呈現，尚非可直接導航的公車路網。
- `oneway` 保留來源 `F` 順線、`T` 逆線、`B` 雙向代碼；`bridge`／`tunnel` 的 `T`／`F` 可能含來源預設值。零值人口、限速及水道寬度不作有效量測，改為 `null`。
- OSM `osm_id` 不保證唯一；`feature.id` 僅在同一快照內以圖層加來源 `fid` 唯一。點與面可描述同一設施，且 OSM 車站不能直接用 ID 串接 TDX 或北市公車資料。

來源與授權：官方界線採[政府資料開放授權條款](https://data.gov.tw/license)，標示「內政部國土測繪中心」。OSM 資料來自 [Geofabrik](https://download.geofabrik.de/asia/taiwan.html)，採 [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/)；地圖應標示「© OpenStreetMap contributors」並連至 [OSM 著作權頁](https://www.openstreetmap.org/copyright)。欄位定義見 [Geofabrik GIS 格式](https://download.geofabrik.de/osm-data-in-gis-formats-free.pdf)。
