# 全國村里逐歲戶籍人口

來源是內政部戶政司的[村里戶數、單一年齡人口（新增區域代碼）](https://data.gov.tw/dataset/77132)，
2018 年起按月發布。以官方目錄中明列的月份選取完整 CSV，沒有憑證或付費 API。
資料採[政府資料開放授權條款第 1 版](https://data.gov.tw/license)；再利用時標示「資料來源：內政部戶政司」。

目前提交 **11509／2026-09** 月檔：7,781 村里、22 縣市、23,343 筆性別記錄。
來源人口合計 23,213,251 人。統計年月、下載時間與詮釋資料更新時間各自分開，
沒有原始發布時間時 `source_data_time` 為 null。

## 抓取、重建與巡檢

Python 3.12+，逐歲 CSV 流程使用標準函式庫；後續里界與站圈使用專案既有的 Shapely、pyproj、pyshp。先依[專案安裝說明](uv.md)執行 `uv sync --locked`，在 repo 根目錄執行：

```bash
# 預設只列計畫；明確指定月份，不追逐「最新」而改變同一指令結果。
uv run -m scripts.official_cross_city village-age --month 2026-09

# 下載官方目錄及該月完整 CSV，檢查後輸出。
uv run -m scripts.official_cross_city village-age --month 2026-09 --fetch

# 需保留原檔及各自的 .source.json receipt；不發出網路請求。
uv run -m scripts.official_cross_city village-age --month 2026-09 --offline

# 由另一份實作直接核對 CSV 與整理檔；任何不一致會以非零結束碼結束。
uv run scripts/review_moi_village_age.py --month 2026-09

# 更新總資料目錄（不會自動把來源整理當作獨立巡檢通過）。
uv run scripts/build_collection_catalog.py
```

也可直接執行 `python -m scripts.official_cross_city.village_age`。`--root` 可將整組來源及輸出放在別處。
若本機 Python 缺少根憑證，應設定有效的系統／組織 CA bundle（`SSL_CERT_FILE`），不可停用 TLS 驗證。

| 路徑 | 內容 |
| --- | --- |
| `raw_data/moi_village_age/moi_catalog_2026-09.html` | 選定月份的官方資源清單快照；本機保留 |
| `raw_data/moi_village_age/moi_village_age_2026-09.csv` | 官方 UTF-8 BOM 原檔；本機保留 |
| 同目錄的 `*.source.json` | 下載 URL、時間、大小及離線完整性核對 receipt；本機保留 |
| `raw_data/moi_village_age/manifest.json` | 可提交的來源目錄，包含來源頁與實際下載 URL |
| `raw_data/moi_village_age/delivery.json` | 可提交的來源交付目錄，供 `data/catalog.json` 使用 |
| `data/population/moi_village_age_2026-09.json.gz` | 可提交的正規化 JSON，壓縮以控制大小 |
| `data/quality/moi_village_age_review.json` | 逐格獨立巡檢與限制；重新產製後須重新巡檢 |

相同原檔、receipt、程式版本與 Python／zlib 環境下，離線重建的 gzip 位元組相同；
gzip 時間欄固定為零，metadata 的 `generated_at` 是固定的 CSV 下載快照參考時間，
不是本次執行轉檔的時間或人口統計日期。重新下載會產生新的 receipt 與來源時間紀錄。

月份不存在、有多個候選連結、下載失敗、格式／加總不符時停止，不以 API 預覽頁、
其他月份或零人口替代，且不覆寫上一次成功的整理檔。

## 村里與年齡查詢

```bash
# 臺北市松山區中崙里，65 歲以上（包含 100+）。
uv run -m scripts.official_cross_city village-age \
  --query data/population/moi_village_age_2026-09.json.gz \
  --area-id 63000010027 --min-age 65 --max-age 100

# 連江縣東引鄉樂華村，女性 18–64 歲，保留前導零。
uv run -m scripts.official_cross_city village-age \
  --query data/population/moi_village_age_2026-09.json.gz \
  --area-id 09007040002 --min-age 18 --max-age 64 --sex female
```

`--min-age` 與 `--max-age` 為包含端點的區間，限制在 0–100；**100 代表整個 100 歲以上組**，
不能藉此查 100 歲整歲、101 歲或任意切割 100+。`--sex` 預設 `total`，可選 `male`、`female`。
可加 `--month` 核對檔案月份。不存在的村里／性別或重複鍵會報錯，沒有資料不回傳零人口。
區間內任一格為 null，查詢人口為 null，並回傳 `missing_age_cells`。

輸出沿用 `schema_version`、`dataset_id`、`metadata`、`records`，查詢檔的
`records[0].population` 是選定年齡層人數，`population_all_ages` 是該村里／性別全部年齡人數。
以鄉鎮市區彙整時只能加總同一月份、同一性別、同一 `district_id` 的村里；
任何缺值應繼續標示未知，不以已知值的部分加總當成完整人口。

## 欄位契約

完整原始欄位清單與對應存於整理檔的 `metadata.source_schema`、`source_field_mapping`。
原始 CSV 必須恰有 210 欄：8 個年月／行政區／戶口欄，以及 202 個逐歲性別欄。

| 整理欄位 | 來源／定義 |
| --- | --- |
| `period`, `year`, `year_roc`, `month` | `統計年月`，民國 11509 轉成 2026-09 |
| `area_id` | 原始 `區域別代碼`，完整 11 碼字串，含前導零 |
| `area_level`, `district_id` | 固定 village；來源碼前 8 碼。此原始整理檔不自行宣告跨來源連接，獨立清冊核對結果另存 crosswalk |
| `county_code`, `county_name` | 來源碼前 5 碼與縣市碼表，檢查 `區域別` 父行政區名稱 |
| `area_name`, `village_name` | 原始 `區域別`（含縣市與鄉鎮市區）、`村里`，罕用字／造字保留原文 |
| `sex`, `population` | male／female 對應 `人口數-男/女`；total 的總數對應原始 `人口數`，單位人 |
| `age_counts` | 101 個人數；索引 0–99 為該整歲，索引 100 為 100+；順序見 `age_counts_order` |
| `households` | 原始 `戶數`，單位戶，只出現在 sex=total，無性別分戶數 |
| `source_file`, `source_row` | CSV 檔名及原始行號，可回查同一來源 |
| `boundary_join_status` | 原始整理檔固定 not_verified；後續已核對的里界狀態見獨立 crosswalk 及地圖檔，不回寫此檔 |

每村里有 male／female／total 三筆，唯一鍵為 `period × area_id × sex`。
total 逐歲格由男女相加，任一格缺值則為 null；總人口保留官方原值。
**total 與男女是重疊統計，三種性別記錄不可一起加總。**
核對每村里男女總數、逐歲合計、同碼唯一、22 縣市存在。缺值使加總驗證為 inconclusive；
`known_sum` 必須連同 unknown 數一起解讀。這些檢查證明下載月檔內部一致及逐列保留；
後續另以戶政司 **11509 獨立村里代碼清冊**，雙向核對全部 7,781 個村里碼、父區碼及原名，
沒有缺碼或名稱差異。清冊完整對應仍不證明人口月份與另一版本里界的幾何一致。

## 公車與地理資料的使用界線

本檔是戶籍人口背景，沒有公車上下車、旅次頻率、通勤方向、旅客 OD 或白天活動人口。
逐歲人口不能直接乘上假設係數，重標為實際乘車需求。

既有 `regional_village_population`、舊人口里界 crosswalk 與臺北近三年年齡檔維持各自來源及期間，
不能直接拼接、相加或沿用舊巡檢結論。新[全國清冊與里界核對](moi-village-age-geography.md)已處理官方 ZIP 所有圖層，
主層為 2026-08-17，Sanhe 附層日期不明；**未取得獨立 2026-09 幾何快照**，`same_month_geometry_verified` 仍為 false。
村里碼與 `base_area_id` 最小統計區碼是不同契約，不用截碼、補零或名稱猜配。
地理連接須另查完整碼、父行政區名稱、異體字與實際里界版本，並揭露未匹配項目。

新 `station_age_catchments.json` 已將北北基完整里界與 2026-09 逐歲人口串接，
對 7,352 站各產生 200／350／500 m 面積分攤估算，站卡使用 350 m。完整估算、已知部分、覆蓋率與 null 分開輸出；
17 個名稱未核實及 7 個幾何衝突村里排除分攤，206 個未編定區塊不補零人口。
村里總數不是站圈人口；另有 `station_catchments.json` 的 **2024-12 最小統計區人口與五歲年齡組、2015 邊界**，
站卡合併為 0–14／15–24／25–64／65+ 四組。這套統計區來源與本資料的三段／規劃分組不同，
不能比較兩者增減、混用分母、混接年齡組或把統計區總人口配上村里年齡人數。
既有[需求落差模型](transport-need.md)使用假設權重與時段係數，其排名差不是缺班數。
現行公車介面在 `data/taipei_feeds/`；計畫班表、到站預估與實際乘車量也必須分開。

## 測試

```bash
uv run python -m unittest discover -s tests/official_cross_city -v
```

CI 使用合成資料測試及已提交地圖資料建置，不連政府 API。全量官方 CSV、清冊、里界及站圈抽樣重算是另外執行的獨立巡檢，
不是合成測試的推論。完整操作、輸出欄位與限制見[村里年齡與站圈](moi-village-age-geography.md)；
原始人口選擇依據見[研究筆記](research/moi-village-age.md)。
