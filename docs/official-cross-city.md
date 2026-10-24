# 官方跨市資料補充

本組只做官方資料取得、正規化與品質檢查；不包含參賽介面、最佳化或需求推估。
來源目錄：[`source_registry.json`](../data/official_cross_city/source_registry.json)。
不改寫既有 `data/taipei_feeds/`、`data/metro/` 或共用 collector。

## 驗證狀態與審查範圍

本 PR 可進入正式程式碼審查，但 **Ready for review 不等於所有資料驗收完成、核准合併或可正式營運**。
初始環境的 DNS 失敗是歷史紀錄，不再代表後續下載結果；2026-10-23 已完成官方來源實抓及
獨立資料核對。這些是有日期的快照檢查，不保證之後的來源仍可用或內容不變。

- 新北路線來源 1,106 列，完全相同列去重後 1,084 列；站牌 33,314 列；跳蛙清單 65 列。
- 2026-09 人口來源 7,781 列，北北基選取 1,652 里，已核對三市同月與人口／性別／年齡加總。
  人口與里界的完整版本對照仍未完成，不能把代碼相符當成完整空間驗收。
- 2026-09 北捷月檔 8,240,400 列；新莊與關渡／士林雙向選取 2,520 列、10,129 人次，
  已逐列對照來源。來源每日只發布 21 個小時，未發布的 02–04 時不補零。
- 新北 PathDetail 的獨立逐列檢查見 [`ntpc_pathdetail_validation.json`](../data/quality/ntpc_pathdetail_validation.json)；
  43,582 筆關聯仍有 114 個參考站牌未連結，保留未知，不猜測關聯。
- 2026-10-24 在助手雲端以 Git blob ID 核對 `3bacca7f82485d2620816ae7fc5663b2840944fe`
  的程式與測試原文後，Python 3.12.14 重跑 81 項離線單元測試、compileall 及四個子命令的
  `--help` 均通過。此次環境沒有先前完整 raw／receipt，**沒有重新下載或重跑完整資料比對**。
- 同一 head 的 [CI](https://github.com/Ti-wb/bus-analysis/actions/runs/39968263553) 在 Python 3.12／3.13 通過；
  這是離線程式測試，不是即時來源、前端 Demo 或正式營運驗收。

大型快照不納入 Git；先前資料檢查與本次離線程式檢查須分開解讀。
產生的新快照仍維持 `independent_review_status=pending`，直到該份快照另行獨立核對。
下方原有驗收要求保留，尚未完成的里界版本、跨來源 ID、基隆／公路客運覆蓋、步行與行車時間
不會因 PR 轉為 Ready for review 而自動通過。

## 來源與範圍

| Collector | 官方來源 | 輸出／限制 |
|---|---|---|
| `ntpc --kind routes` | 新北市交通局，data.gov.tw **123112** | 主線×附線×業者；班距與首末班文字不是逐班班表 |
| `ntpc --kind stops` | 新北市交通局，data.gov.tw **123091** | 站牌、方向、站序、上下車原碼、座標；不自動推論附線或碼表 |
| `ntpc --kind leapfrog` | 新北市交通局，data.gov.tw **174364** | 跳蛙清單與營運描述；沒有可核實的 route ID 就不靠名稱關聯 |
| `population` | 內政部，data.gov.tw **77132** | 同一指定月份的臺北／新北／基隆里人口；不是白天人流或通勤 OD |
| `metro-od` | 臺北捷運，data.gov.tw **128506** | 保留指定走廊日期×小時×起訖站人次；不是公車潛在需求 |

以上五組依官方目錄採政府資料開放授權條款第 1 版並保留提供機關。官方網站不自動等於
開放授權；TDX、基隆官網、預約平台等其他來源須另外確認使用條款，不能繼承上述授權。

本次**不宣稱完成**：基隆市區公車／公路客運全量、跨來源 ID 對照、逐線站間時間、
合法步行轉乘圖、核定容量、訂票量或駕駛勤務。這些仍是「未知」，不能當成沒有服務。

## 執行

在 repo 根目錄執行；使用 Python 3.12+ 標準函式庫，不需額外套件。`uv run` 亦可。
預設只列出計畫，只有明確指定 `--fetch` 才連線；不使用 API key，不呼叫付費 TDX API。

```bash
# 計畫與說明：不下載、不覆蓋既有資料。
python -m scripts.official_cross_city ntpc
python -m scripts.official_cross_city population --month 2026-09
python -m scripts.official_cross_city metro-od --help

# 分別從官方完整 CSV 匯出下載，不使用 API 預覽的前幾筆當作全量。
python -m scripts.official_cross_city ntpc --kind routes --fetch
python -m scripts.official_cross_city ntpc --kind stops --fetch
python -m scripts.official_cross_city ntpc --kind leapfrog --fetch

# 只有官方目錄真的列有該月才會選用；不存在或歧義時停止，不猜測網址。
# 2026-09 曾於 2026-10-23 驗證；後續下載仍需重新檢查來源與快照。
python -m scripts.official_cross_city population --month 2026-09 --fetch

# 利用現有 data/metro/source_references.json 已紀錄的官方月檔網址。
# 來源進／出站名稱須精確匹配；命名不一致就停止，不猜測站名對照。
# 2026-09 的頭前庄進站名為「頭前庄」、出站名為「O頭前庄」；此範例不納入該站，避免猜測異名映射。
python -m scripts.official_cross_city metro-od \
  --month 2026-09 --corridor-id xinzhuang-guandu \
  --origins '新莊' --destinations '關渡,士林' --bidirectional --fetch

# 離線重建：需要本組先前下載的原檔與匹配 .source.json receipt。
python -m scripts.official_cross_city ntpc --kind routes --offline
python -m unittest discover -s tests/official_cross_city -v
```

原檔與 HTTP receipt 在 `raw_data/official_cross_city/`，預設不進 Git。
正規化成功才以 atomic replace 寫入 `data/official_cross_city/` 專屬檔案。
不更新其他 collector 或全域目錄；獨立巡檢通過後再整合至共用目錄。
`--root` 僅用於隔離輸出或測試；`--encoding cp950` 必須經確認來源編碼後才指定。

## 失敗與 schema drift

- HTTPS 精確 host allowlist；每次 redirect 事前檢查；不關閉 TLS 驗證、不繞過防護。
- HTTP 206、空檔、截斷、超出大小上限、HTML 錯誤頁、重複欄位、資料列欄數錯誤均停止。
- 新北每檔最多 80 MiB；人口 80 MiB；北捷月檔 600 MiB。磁碟空間不足就停止。
- 新北完整匯出筆數只表示取得檔案的列數，不能證明整個營運系統全量覆蓋。
- 新北多業者保留；只有完全相同的列合併且保留來源行號。同鍵不同內容不任意挑一筆。
- 人口 parser 目前對應官方目錄的中文欄位；若實檔改成英文字段或不同封裝就停止。
  需先取得實檔核對映射，再補測試，不能把未驗證欄名推測標為已支援。
- 每里驗證男+女=總人口，以及可取得時單歲和=性別總數。缺值仍是 null，不是 0。
- 不推測不同年代里界的行政代碼對應；`boundary_join_status=not_verified`。
- 北捷串流處理全月，僅保留選定站對（上限 250,000 列）。來源需以日期×小時×進站
  分組連續；重複群組或 OD cell 停止。進／出站字典分開核對，缺列不補零。
- 北捷跨月列保留實際日期，`within_source_month=false`；`calendar_weekday` 不是營運日曆。
- `selected_cells_not_observed` 是相對於來源出現的日期小時×所選站對的未觀測格數，
  不是需求為零的格數。`known_*_sum` 只加總已知值，缺值數必須一起顯示。
- `downloaded_at` 與 HTTP `Last-Modified` 不是官方統計期間或逐筆觀測時間。

## 資料驗收要求（未全部完成）

1. 在可連線環境依序執行官方下載；核對 HTTP receipt、實檔欄位、期間、列數與授權。
2. 由另一位 reviewer/Agent 巡檢來源與欄位，將結果另存 `data/quality/`；不得把本測試
   結果當成獨立巡檢。未巡檢的輸出保持 pending。
3. 對跨市範圍建立 covered/missing/unknown 清單；缺基隆或客運就不發布全北北基涵蓋率。
4. 抽核主線／附線／業者、方向、重複站與跨市站牌；未核實的 ID mapping 不可用。
5. 比對捷運選定 OD 的原始列與加總；人口要確認三市同月以及里界版本對照。
6. 驗證解析失敗不改寫上一版正規化資料；再把經檢查的小型產物加入 commit。

## 分析邊界

人口多且車上回報少，不足以證明存在未滿足需求；也可能是其他運具、時段或觀測覆蓋差異。
相鄰負載差不是可唯一識別的上車、下車或 OD；計數延遲與漏站會改變歸屬。
IPF／人口權重只能提供依賴先驗的估計，不能把它重新標成觀測。
路段車上人數不能替代站點上下車門檻。未知、過期、沒服務、零需求是不同狀態。
本組不從路線重疊率或低負載直接給出減班／DRTS 結論。

## 新北附線與站牌明示關聯

`ntpc-pathdetail` 使用官方 [公車動態資料目錄](https://data.taipei/dataset/detail?id=2a8f5f42-942c-4974-8366-b44e90ad9701)
及 [新北規格 v5.5](https://pto.gov.taipei/News_Content.aspx?n=A1DF07A86105B6BB&s=C668AE404DCD2EFE)
列出的 `ntpcbus/GetPathDetail.gz`。目錄標示公開，授權依該站
[政府資料開放授權條款第 1 版](https://data.taipei/rule)，不繼承其他新北目錄的授權。
先取得本組 routes、stops 快照，再以官方 `pathAttributeId`、`stopId` 核對並保留一對多關聯；
未知 ID、矛盾主線、重複 ID／序號格、缺必要欄位即停止，不猜名稱或補造站序。

```bash
python -m scripts.official_cross_city ntpc-pathdetail --help
python -m scripts.official_cross_city ntpc-pathdetail --fetch
python -m scripts.official_cross_city ntpc-pathdetail --offline
```

壓縮下載上限 8 MiB、解壓上限 32 MiB、100,000 列；預設仍只列計畫。
輸出為 `data/official_cross_city/ntpc_pathdetail.json`，原檔與 receipt 保留於既有 raw 目錄。
`source_record_number` 是 JSON 陣列中從 1 起算的位置，不是 CSV 行號。
`type`、`typeAttribute` 保留原碼，不擅自套用方向或跳蛙分類。
官方新北端點的 `EssentialInfo.Location` 曾標示「台北市」，原文保留並明列異常，
不能因此改列臺北來源。參考快照與端點時點可能不同，不能假定所有缺碼都是永久缺漏。

2026-10-23 實抓來源時間 `2026/10/23 21:02:56`，43582 筆明示關聯覆蓋
1008 個附線、33200 個站牌；相對本次 33314 個站牌快照仍有 114 個未連結。
這是下載快照的 ID 關聯驗證，不代表即時營運、全北北基服務覆蓋或跳蛙路線 ID 對照。
2026-10-23 的後續重建重用當時原檔與 receipt，不宣稱再次下載；大型產物不納入 Git。
