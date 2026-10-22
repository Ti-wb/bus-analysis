# 官方跨市資料補充（Draft）

本組只做官方資料取得、正規化與品質檢查；不包含參賽介面、最佳化或需求推估。
來源目錄：[`source_registry.json`](../data/official_cross_city/source_registry.json)。
不改寫既有 `data/taipei_feeds/`、`data/metro/` 或共用 collector。

## 本次狀態

- 官方目錄、欄位與授權已核對；**尚未完成本環境的官方完整檔下載及端到端驗證**。
- 本環境不能解析外部主機名稱；另以閱讀工具取 CSV 時遇到內容類型限制／來源防護。
  不能因此宣稱來源已下架，也不能產生空資料代替它。
- 單元測試使用明示的合成資料，只驗證解析和防錯行為，不代表官方資料內容正確。
- Repo 要求的另一位 Agent 獨立巡檢尚未執行；所有產物的 `independent_review_status` 為 `pending`。
- 不提交任何假造的政府資料快照。轉為 ready 前必須實抓、檢查並完成獨立巡檢。

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
# 2026-09 是示範輸入，不是聲稱已驗證該月資源。
python -m scripts.official_cross_city population --month 2026-09 --fetch

# 利用現有 data/metro/source_references.json 已紀錄的官方月檔網址。
# 來源進／出站名稱須精確匹配；命名不一致就停止，不猜測站名對照。
python -m scripts.official_cross_city metro-od \
  --month 2026-09 --corridor-id xinzhuang-guandu \
  --origins '新莊,頭前庄' --destinations '關渡,士林' --bidirectional --fetch

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

## 轉為 ready 的驗收

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
