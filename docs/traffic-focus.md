# 臺北市交通需求候選設施

可用 OSM 建立設施位置與範圍，再用官方名冊、學生數、病床數與住宅戶數補強。這份清單用於尋找潛在需求點，**不是已驗證的高流量地點排名**。目前交付可匯入 GIS 的資料，尚未整合到地圖前端。

## 已整理清單

OSM 使用專案既有 **2026-10-20 20:23:47 UTC** 快照，依官方臺北市界篩選，共 **2,424 筆來源圖徵**。同一學校、醫院或社區可能有多個圖徵，以下不是不重複設施數。

| 類別 | 圖徵數 | 目前能做的評估 | 尚需補強 |
| --- | ---: | --- | --- |
| 學校與大專院校 | 359 | 找校園、校區附近的大眾運輸服務 | 學生及教職員數、學制、出入口、上下課時段 |
| 醫院 | 47 | 找醫療設施與院區附近的服務缺口 | 官方醫院名冊、病床、門急診人次、入口；部分圖徵可能是院內單位 |
| 百貨、商場與市場 | 189 | 找採買目的地 | 營業狀態、樓地板面積、平假日人流 |
| 文教與運動場館 | 430 | 找圖書館、博物館、劇場與體育設施 | 場館規模、活動容量、開放時間；包含校內及小型設施 |
| 軌道站與轉運設施 | 102 | 找轉乘節點 | 官方站碼對照、實際出入口與運量；排除一般公車站牌 |
| 市政與司法設施 | 42 | 找洽公目的地 | 上班與洽公人次、辦公時間 |
| 公園與遊憩設施 | 178 | 找休閒目的地 | 活動及假日人流；公園限具名且市內面積至少 1 公頃 |
| 具名住宅區候選 | 809 | 找待核實的居住區域 | 社區身分、戶數、實住人口、出入口；含金瑞里等一般地名，並非 809 個大型社區 |
| 具名商業與產業區候選 | 268 | 找待核實的工作目的地 | 就業人數與用途；也含倉庫、機房等，不能全視為就業集聚點 |

- [OSM 設施清單 CSV](../map-data/traffic-focus/osm/candidates.csv)：名稱、分類、行政區、經緯度、來源、同名提示。
- [OSM 標記 GeoJSON](../map-data/traffic-focus/osm/candidates.geojson)：2,424 個標記。
- [OSM 範圍 GeoJSON](../map-data/traffic-focus/osm/areas.geojson)：其中 2,039 筆有面範圍，與標記共用 `candidate_id`，不可相加。

另已下載並整理臺北市都發局社宅工程 API。來源未提供資料更新時間，下載時間另記於 [manifest](../map-data/traffic-focus/housing/manifest.json)，不能把下載日當資料日期。

| 官方社宅來源處理結果 | 筆數 |
| --- | ---: |
| 可定位單案 | 75 |
| 可定位附屬公共設施 | 3 |
| 臺北市單案缺座標，保留清單但不定位 | 4 |
| 多案合計列，保留但不當成單一地點 | 2 |
| 位於新北市，排除 | 2 |
| 原始 API 全部列 | 86 |

- [社宅完整清單 CSV](../map-data/traffic-focus/housing/candidates.csv)：84 列臺北市來源資料，含未定位及合計列。
- [社宅與附屬設施 GeoJSON](../map-data/traffic-focus/housing/candidates.geojson)：78 個可定位標記。
- [未定位與合計列 JSON](../map-data/traffic-focus/housing/unmapped.json)：6 列待處理項目。

以**規劃戶數至少 300 戶**作為可調整的初步篩選，已定位者有 22 案，其中 17 案來源標示已完工。例如南港機廠社宅 1 區（1,442 戶）、六張犁社宅（722 戶）、東明社宅（700 戶）。這是研究門檻，並非官方「大型社區」定義；規劃戶數、完工與實際入住是不同資訊。

## 可補強的資料來源

下列官方來源已查核資料頁的欄位或功能；除社宅 API 外，**尚未匯入或與 OSM 配對**。

| 來源 | 可取得或查核的資訊 | 使用方式及限制 |
| --- | --- | --- |
| [Geofabrik 臺灣 OSM](https://download.geofabrik.de/asia/taiwan.html) | 設施點、校園／院區、住宅區、建物與交通設施 | 本次使用已整理快照；免費 GIS 匯出並非完整 tags，不含可靠人流或完整容量 |
| [臺北市各級學校分布資料](https://data.gov.tw/dataset/121225) | 校名、類別、地址與聯絡資料 | 可核實學校名冊；頁面欄位沒有列座標與學生數，也不是全體大學完整清單 |
| [教育部學校地理資訊及地區別統計](https://stats.moe.gov.tw/edugissys/default.aspx) | 各學制學校、地理查詢、全校學生數條件 | 可查核位置與規模；批次取得與校區分配尚待確認，校本部人數不可套到每個校區 |
| [臺北市公私立醫院病床數統計](https://data.gov.tw/dataset/132504) | 醫院名稱、地址及各類病床數 | 可補醫院規模；需核對統計期、院區與床別，不把病床換算成每日旅次 |
| [臺北市公寓大廈組織報備](https://data.gov.tw/dataset/132391) | 執照號碼、行政區、管委會名稱與設置地址 | 私人社區的重要候選名冊；現有欄位沒有戶數及經緯度，也可能是商辦，需核對用途與地址定位 |
| [臺北市社會住宅興建工程進度](https://data.taipei/dataset/detail?id=659c3565-df41-4f80-915f-95e83071bdcd)／[API](https://hms.udd.gov.taipei/api/BigData/project) | 座標、規劃戶數、來源人口欄、工程進度 | 本次已整理；住宅、附屬設施、缺座標與合計列分開，來源人口欄不當作已核實實住人口 |
| [專案人口資料目錄](../data/population/catalog.json) | 里／統計區人口與年齡結構 | 可評估周邊需求；必須核對年份及區域代碼，不可把整里人口直接當作社區人口 |
| [專案捷運分時進出量](../data/metro/station_hourly_flows.json) | 站點、時段與進出量 | 可作轉乘需求參考；需另核對車站身分，OSM ID 不能直接當官方站碼 |

OSM 的 [landuse=residential](https://wiki.openstreetmap.org/wiki/Tag:landuse%3Dresidential) 表示住宅用途區域，可能是街廓或具名住宅開發，單靠此標籤無法確認大型社區。私人社區建議先從官方報備名冊確認名稱及用途，再補戶數；不以 OSM 面積代替住戶規模。

## GIS 使用

GeoJSON 可直接加入 QGIS、MapLibre 或 Leaflet，座標為 WGS84 `[經度, 緯度]`；社宅來源未明示 datum，WGS84 為根據經緯度及市界核對的推定。總目錄 [map-data/catalog.json](../map-data/catalog.json) 已納入圖層與巡檢結果。

1. 依 `category` 分類顯示；OSM 的住宅與產業 landuse 預設作待核實區域，不與已核實設施混算。
2. 社宅可篩 `record_kind = project`、`is_completed = true`、`meets_household_threshold = true`。施工及規劃案件另外顯示，避免當成現況需求。
3. 選取標記時呈現來源、原分類、規模、工程狀態與資料限制。OSM `same_name_group_id` 是同名提示；社宅 `same_location_group_id` 提醒不同案共用座標，需讓重疊標記可選取。
4. 社宅「中山站聯開宅」來源行政區是大同區，座標落中山區，已標 `location_review_required=true`；先核對位置再做距離分析。
5. 下一階段可疊合公車站牌、路線、班次、捷運及人口，計算步行可達性或服務缺口。面內標記不是出入口，不應直接當作步行起訖點；直線距離也不等於步行距離。

`student_count`、`bed_count`、`household_count`、`daily_visitors`、`daily_trips` 等未取得欄位為 `null`，CSV 對應空白，不可補 0。兩個來源尚未對照去重；同名、同址不自動代表同一設施，也不以未驗證 ID 連接來源。

## 更新與檢查

```bash
# 由現有 OSM/admin 檔案重建；不下載全臺資料
uv run scripts/build_traffic_focus_osm.py
# 下載小型官方 API；--offline 使用 map-data/raw/traffic-focus/housing/ 快取
uv run scripts/update_traffic_housing.py
# 可調門檻（同樣需重查後再使用）
uv run scripts/build_traffic_focus_osm.py --park-min-area-m2 10000
uv run scripts/update_traffic_housing.py --offline --large-households 300
uv run python -m unittest discover -s tests -p test_traffic_focus.py -v
```

每次重建後由獨立 Agent 巡檢，結果放 [traffic_focus_review.json](../map-data/quality/traffic_focus_review.json)，再執行 `uv run scripts/build_map_catalog.py`。巡檢須核對來源快照、圖徵數、分類、座標、缺值、重複與戶數篩選。社宅 API 無來源日期，目錄只用下載收據識別巡檢過的快照，不把它填成來源更新日。同一快照改動篩選規則或程式也要重查。

原始社宅檔只存 `map-data/raw/traffic-focus/housing/`，排除 Git；腳本僅負責各自的 `traffic-focus/osm/`、`traffic-focus/housing/`。OSM 需標示「© OpenStreetMap contributors」，適用 [ODbL](https://www.openstreetmap.org/copyright)；官方資料標示提供機關並遵守資料頁授權。
