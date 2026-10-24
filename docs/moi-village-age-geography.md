# 村里年齡、里界與站圈

本流程將內政部 **2026-09 逐歲戶籍人口**，先與同月獨立村里清冊核對，再連接明示版本的官方里界。
全國核對保留遠島；地圖及站圈沿用北北基範圍。人口是居住背景資料，**不是搭乘量、通勤 OD、白天活動人口或實際乘車需求**。
逐歲原檔抓取與年齡查詢另見[人口月檔說明](moi-village-age.md)。

## 來源與版本

| 資料 | 官方來源 | 本次版本與涵蓋 |
| --- | --- | --- |
| 逐歲人口 | 戶政司[資料集 77132](https://data.gov.tw/dataset/77132) | 11509／2026-09，22 縣市、7,781 村里；每里 male／female／total 三筆，共 23,343 筆；僅 total 加總為 23,213,251 人 |
| 獨立村里清冊 | 戶政司[行政代碼頁](https://www.ris.gov.tw/documents/html/5/1/167.html)、[11509 UTF-8 原檔](https://www.ris.gov.tw/documents/data/5/1/11509VillageUTF8.txt) | 7,781 筆無標題三欄：11 碼村里碼、8 碼父區碼、原名；與人口雙向逐碼、父碼及原名全部一致 |
| 全國里界 | 國土測繪中心[資料集 7438](https://data.gov.tw/dataset/7438) | 同一 ZIP 內 `VILLAGE_NLSC_1150817` 主層 7,986 筆與 `Village_Sanhe` 附層 1 筆；各自 PRJ 為 EPSG:3824 |

主層版本 **2026-08-17** 可由 SHP 檔名及 ZIP 內 XML 修訂日期核實。
Sanhe 附層沒有可證明幾何日期的欄位或日期檔名，`boundary_vintage` 保留 null；ZIP 檔案時間不能當幾何日期。
目前下載成果不是 2026-09 歷史幾何快照，`same_month_geometry_verified` 一律 false。
統計月份、幾何版本、下載時間及來源資料時間各自保留，沒有來源時間時維持 null。

人口與清冊採戶政司[政府網站資料開放宣告](https://www.ris.gov.tw/app/portal/133)，里界授權見官方資料集；
均依[政府資料開放授權條款第 1 版](https://data.gov.tw/license)再利用，標示「資料來源：內政部戶政司、內政部國土測繪中心」。

## 核對及排除規則

人口、清冊及所有 SHP 圖層以完整原碼核對，不以相同筆數、名稱或補零猜配。
原始 Unicode、父行政區及兩端名稱保留，不能全域替換異體字。

| `boundary_join_status` | 本次筆數 | 分攤處理 |
| --- | ---: | --- |
| `verified` | 7,751 | 完整碼、父行政區及原名一致，可供分攤 |
| `verified_alias` | 6 | 僅六個具官方文件證據的完整「碼、行政區、兩端原名」組合可用，見[名稱證據](boundary-evidence.md) |
| `name_mismatch` | 17 | 保留來源人口及兩端原名，未核實前排除分攤 |
| `geometry_conflict` | 7 | Sanhe 與六個主層村里有正面積交疊，雙側全部排除 |
| `unassigned` | 206 | 官方未編定村里圖形，沒有對應人口；維持 null，不視為零人口 |

合計 **7,987 個圖形＝7,781 個人口村里＋206 個未編定區塊**。
17 個名稱未核實加上 7 個幾何衝突村里，共 **24 村、來源人口 45,064 人**，只保留來源數值，不參與面積估算。
行政碼能對應不代表圖形互斥，也不代表人口月份當時的幾何相同。
未編定區塊的碼可含字母；這種碼只在官方 `NOTE=未編定村里` 且村里名空白時接受，不能拿來配人口碼。

北北基完整地圖檔共 **1,677 個圖徵**：臺北市 456、新北市 1,056、基隆市 165，包含 25 個未編定區塊。
六筆已核實名稱別名均在新北市；其餘全國未審名稱及 Sanhe 衝突仍留在全國核對表，不因地圖範圍較小而省略。

## 抓取與離線重建

在 repo 根目錄執行，需 Python 3.12+ 及鎖定的 Shapely、pyproj、pyshp 依賴。

```bash
uv sync --locked

# 下載選定月份的官方人口 CSV；不省略月份追逐最新版。
uv run -m scripts.official_cross_city village-age --month 2026-09 --fetch

# 下載獨立清冊、里界目錄與完整 ZIP，逐層核對後產生全國表及北北基圖層。
uv run -m scripts.official_cross_city.national_villages --month 2026-09 --fetch

# 原檔及每個 .source.json receipt 都保留後，可不連網重建。
uv run -m scripts.official_cross_city village-age --month 2026-09 --offline
uv run -m scripts.official_cross_city.national_villages --month 2026-09 --offline

# 獨立巡檢來源人口、全國清冊、全部 SHP 層及北北基每筆完整座標／101 歲別。
uv run scripts/review_moi_village_age.py --month 2026-09
uv run scripts/review_village_age_geography.py --month 2026-09

# 產生地圖副本與站圈；使用 repo 既有公車、OSM、YouBike 等來源檔。
uv run scripts/build_web_data.py
uv run scripts/review_village_age_geography.py --month 2026-09 --stations
uv run scripts/build_collection_catalog.py

# 啟動地圖或建置靜態網站。
npm ci --prefix web
npm run dev --prefix web
# 結束開發伺服器後，可執行：
npm run build --prefix web
```

`national_villages` 未加 `--fetch`／`--offline` 時只列計畫。離線模式驗證原檔與 receipt 的完整性，
缺檔、來源不符、重複碼、未知圖層、座標系統或無效圖形會停止，不使用其他月份或零值替代。
兩個整理流程均可用 `--root` 指定資料根目錄；前端建置使用 repo 內路徑。
若需設定組織 CA，使用有效的 `SSL_CERT_FILE`，不可停用 TLS 驗證。

## 輸出與欄位

| 路徑 | 用途 |
| --- | --- |
| `raw_data/national_village_age/` | 官方清冊、目錄頁及 receipt；原檔不提交，來源 `manifest.json`／`delivery.json` 可提交 |
| `map-data/raw/national_village_age/villages.zip` | 官方完整 ZIP 及 receipt；本機保留，不提交 |
| `data/population/ris_village_registry_2026-09.json.gz` | 一筆一村里的清冊：`village_code`、`district_code`、`village_name`、原始行號 |
| `data/population/national_village_crosswalk_2026-09.json.gz` | 人口、清冊及所有里界碼的聯集核對表；包含未匹配及排除項目 |
| `map-data/population/moi_village_age_2026-09.geojson.gz` | 北北基完整精度里界及人口、逐歲／分組值；站圈計算來源 |
| `web/public/data/village_age.geojson` | 僅供顯示的簡化里界，保留相同 ID、來源人口及年齡欄位 |
| `web/public/data/station_age_catchments.json` | 全量檢查基準，`records[bus\|mrt\|youbike][原始站點 ID]` 按 `radii` 順序放各半徑結果 |
| `web/public/data/station_age_catchments/part-00.json` 至 `part-31.json` | 供站卡依站種／完整原碼載入其中一片；32 片聯集與全量每筆值相同 |

年齡圖形、全量站圈及每個分片另產生內容相同的 `.gz` 副本；`web/public/data/` 是建置產物，不提交 Git。
全量站圈約 122.1 MB JSON／7.5 MB gzip，保留作驗證基準。站卡以 `FNV1a32(UTF-8(站種 + ":" + 完整站碼)) % 32` 選片，
每片最大約 3.9 MB JSON／264 KB gzip，32 片 gzip 合計約 8.2 MB；每片 metadata 的 `shard` 明列 algorithm、index、count=32 與該片站數。
Metadata 保存 `population_period`、`roster_period`、主層 `boundary_vintage`、逐層版本及筆數、來源 receipt、授權及限制。
`generated_at` 識別固定來源下載快照；前端 `built_at` 才是本次建置時間，兩者都不是人口統計日期。

Crosswalk 每筆保存 `area_id`、三種來源是否存在、兩端原名、`district_id`、`roster_match_status`、
`boundary_identity_status`、最終 `boundary_join_status`、`allocation_eligible`、`source_population`、
`boundary_layer`、逐層 `boundary_vintage`。來源人口沒有因不合格而改成零。

GeoJSON 使用 WGS84 `[經度, 緯度]`，properties 保存 `area_id`、`boundary_id`、原始及里界名稱、
`population`、101 格 `age_counts`、`age_groups`、核對狀態與來源備註。
索引 0–99 為整歲，索引 100 代表 **100 歲以上全部人口**，不是精確 100 歲。

| 分組集合 | 欄位及區間 |
| --- | --- |
| `three_stage` | `age_0_14`：0–14；`age_15_64`：15–64；`age_65_plus`：65+ |
| `planning` | `age_0_5`：0–5；`age_6_17`：6–17；`age_18_64`：18–64；`age_65_74`：65–74；`age_75_plus`：75+ |

各集合分別涵蓋全齡，**兩套集合互相重疊，八組不可一起加總**。6–17 歲是年齡區間，不等於在學學生。

## 站圈估算與缺值

本次依實際顯示點位，對 **38,372 個公車站碼（5,376 個原臺北站碼＋32,996 個跨市站碼）、154 捷運站、1,822 YouBike 站，共 40,348 站**，各產生 200／350／500 m 結果，共 121,044 筆。
本次為 112,402 筆 complete、7,962 筆 partial、680 筆 no_boundary；無里界者保留未知，不刪除或補零。跨市數量包含來源已分開的座標變體，保留完整原碼，不依同名或近鄰合併。
三種原始站點 ID 命名空間分開，不把捷運或 YouBike 對上鄰近公車站借用估算。
站卡固定使用 350 m，與覆蓋圖層的使用者偏好半徑分開。

計算使用未簡化完整里界，轉 EPSG:3826 後產生 256 邊直線圓，逐村里計算：

```text
該村里在站圈內的估算人口 ＝ 村里人口 × 交集面積 ÷ 完整村里面積
```

每個年齡組使用同一面積比例，假設村內各年齡人口均勻分布。投影僅支援明列的北北基及鄰近範圍，
不套用至全國站點或遠島；圓圈不考慮步行路網、河川阻隔或捷運出入口。
兩個里界正面積重疊超過 0.01 m² 時，雙側圖形均排除估算；共用邊線不視為衝突。

| 結果欄位 | 定義 |
| --- | --- |
| `population_estimate` | 全齡已知人口圖形涵蓋完整圓時的估算；不完整則 null |
| `population_known_estimate` | 只以可用且全齡人口有值的圖形計算已知部分；完全沒有可用來源則 null |
| `age_group_estimates` | 各組另行判定：人口有值且組內每個歲別都有值的圖形須涵蓋完整圓，否則該組 null |
| `age_group_known_estimates` | 各組僅計算已知完整歲別的部分，不能宣稱是完整站圈人口 |
| `status` | `complete`：全齡及八組皆完整；`partial`：部分涵蓋或缺值；`no_boundary`：無里界；`geometry_conflict`：相交里界有正面積重疊衝突 |
| `*_village_count` | 相交、未匹配、人口缺值、整體未知、幾何衝突及逐組未知的村里數；這些計數可能重疊，不能相加當總數 |

`partial` 不等於每個欄位都缺值：某個歲別未知只影響包含該歲的分組，全齡與其他分組可各自有完整值。
但站卡在整體不完整時只列已知部分，不用部分人口當成完整比例的分母。
已發布人口為 null 時不從歲別合計反推完整全齡；實測來源為 0 則保留 0，比例因無分母而未知。

覆蓋率均為 **站圈面積比例**，不是人口涵蓋比例、信賴度或可步行到達比例。以幾何聯集計算，不加總重疊面積：

| 覆蓋欄位 | 分子；分母一律為完整站圈面積 |
| --- | --- |
| `boundary_share` | 所有相交里界的聯集面積，包含未匹配圖形 |
| `data_share` | 已核對且無重疊衝突圖形的聯集面積 |
| `population_data_share` | 上述圖形中全齡人口有值者的聯集面積 |
| `age_group_data_shares` | 上述圖形中人口有值、該組所有歲別也有值者的聯集面積，各組分開 |
| `outside_boundary_share` | 1 − `boundary_share`；此範圍人口未知，海岸外亦不推定為零 |

完整性使用未四捨五入比例，僅容許 `1e-8` 浮點誤差；輸出的比例保留六位小數，
因此應同時看 status 與完整估算是否為 null，不能僅憑畫面顯示「100%」認定完整。
各站圈會重疊，不能加總為不重複人口。

## 在地圖查詢

「偏好 → 分析圖層 → 村里年齡 → 套用設定」，或按左側「查看村里年齡」。
左側可切換三段／細分組及選定年齡層，地圖以占該村里戶籍人口的比例上色；灰色代表未匹配、缺值或比例無分母。
點選村里或從下拉選單選擇，可查原碼、名稱差異、來源人口、月份、版本及限制。
方向鍵移動地圖後 Enter 查詢中央村里，Escape 關閉；部分圖形在初始視野外。

站點資訊卡另有「村里年齡分攤」，可切換分組並查看 350 m 已知部分、逐組涵蓋面積、未匹配及界外資訊。
資料載入失敗可重試。年齡圖層按需載入壓縮副本；站卡只載入該站所屬的 32 分片之一，保留全量檔供獨立檢查，不在開卡時下載全量。格式錯誤不以零值替代。

`station_catchments.json` 使用 **2024-12 最小統計區人口、五歲年齡組、2015 邊界、臺北市範圍**，
其站卡年齡為 **0–14／15–24／25–64／65+** 四組，來源見 `data/population/taipei_statistical_population_age5_base.json`。
本流程另使用 **2026-09 村里逐歲人口、2026-08-17 主里界、北北基範圍**，產生三段及規劃兩套分組。
兩套結果都有年齡資料，但基準、粒度及分組不同；不能直接比較人口增減、混接分組、混算比例，
也不能把統計區總人口搭配村里年齡人數。
既有需求落差權重及路線方案影響模型未因此換成人口或年齡的實測乘車需求模型。

## 測試與獨立巡檢

```bash
uv run python -m unittest tests.official_cross_city.test_national_villages -v
uv run python -m unittest tests.test_age_catchments -v
uv run python -m unittest tests.official_cross_city.test_village_age_geography_review -v
npm run test:age --prefix web
```

- [全國清冊及人口／里界報告](../data/quality/national_village_age_review.json)與[完整地圖圖形報告](../map-data/quality/village_age_geography_review.json)：不同於整理者的實作直接讀 TXT、全部 DBF／SHP／PRJ／XML，不匯入整理器；完整比對碼集合、父碼、原名、層版本、北北基座標、101 歲別及八組。
- [站圈獨立報告](../data/quality/station_age_catchments_review.json)：全量核對 40,348 站的原始 ID、原臺北與跨市公車 ID 無碰撞的完整聯集、121,044 個半徑／狀態紀錄及里界拓樸；保留完整、未匹配、界外邊緣三類抽樣，另加一個跨市原碼站，各重算三個半徑的人口、分組與聯集覆蓋，共 12 個站圈。另完整比對 32 分片的 UTF-8 hash 路由、每筆值與 metadata 聯集、無重複／掉列及 gzip／JSON 等價，未宣稱逐一重算全部站圈的幾何估算。
- 測試會拒絕同筆數錯碼、Sanhe 借用主層日期、名稱／座標／100+ 格移動、未知人口補零，以及估算覆蓋不完整等情形。CI 以合成資料及已提交輸出測試，不連政府 API；本機原檔巡檢只對報告所列來源快照負責。

仍待釐清的來源限制是同月歷史幾何、17 筆名稱差異與 Sanhe 重疊。均勻面積假設也未經居住位置或乘車樣本校準；
查閱結果時應保留這些限制。開源選擇與直接使用方式見[研究依據](research/village-age-geography.md)。
