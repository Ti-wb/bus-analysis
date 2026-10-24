# 官方跨市資料

整理官方路線、站牌、里人口與捷運 OD，供跨市公車研究使用。
各來源的時間、範圍、單位與限制以輸出 metadata 為準；基礎 collector 來源目錄見
[`source_registry.json`](../data/official_cross_city/source_registry.json)。

## 資料及範圍

| 資料 | 官方來源 | 主要限制 |
|---|---|---|
| 新北路線、站牌、跳蛙清單 | data.gov.tw [123112](https://data.gov.tw/dataset/123112)、[123091](https://data.gov.tw/dataset/123091)、[174364](https://data.gov.tw/dataset/174364) | 主線、附線、業者、方向分開；清單沒有明示 ID 時不猜測 |
| 新北附線站序 | [官方公車動態目錄](https://data.taipei/dataset/detail?id=2a8f5f42-942c-4974-8366-b44e90ad9701)、[規格 v5.5](https://pto.gov.taipei/News_Content.aspx?n=A1DF07A86105B6BB&s=C668AE404DCD2EFE) | 只有本快照明示的主線／附線／站牌關聯；未出現的站牌不推論停駛 |
| 北北基里人口 | [內政部 77132](https://data.gov.tw/dataset/77132) | 戶籍人口，不是通勤或公車需求 |
| 人口與里界對照、基隆里界 | [國土測繪中心 7438](https://data.gov.tw/dataset/7438) | 分開記錄人口月份和里界版本；造字／異體字差異及未編定村里另列 |
| 捷運走廊 OD | [北捷 128506](https://data.gov.tw/dataset/128506) | 保留來源原列與實際小時，不把未發布時段補零 |
| 新北、基隆及公路客運路線主檔 | [TDX 歷史服務](https://tdx.transportdata.tw/data-service/historical) | 依來源 Count 核對指定日主檔，不等於班表、站序及實際營運全量覆蓋 |

ID 對照用法見 [ID 對照說明](id-crosswalk.md)；新增站間旅時與道路資料分別見
[公車站間旅行時間](tdx-bus-travel-time.md)及[道路速率與流量](tdx-road-traffic.md)。

逐項補證見[新北與跳蛙 ID](id-evidence.md)、[臺北 ID 與完整站序](taipei-id-evidence.md)、
[里名及里界版本](boundary-evidence.md)、[區域站序與服務日曆](regional-service-coverage.md)、
[北北基地理會員與發布覆蓋](regional-service-evidence.md)、[步行轉乘](walking-evidence.md)及
[過街與端點補證](walking-further-evidence.md)。
旅時的附線、方向、相鄰站對、週末／平日時段及未覆蓋列，見[站對覆蓋證據](travel-time-coverage.md)。

## 執行

Python 3.12+；以下基礎 collector 使用標準函式庫，地圖對照另需專案 GIS 依賴。
使用 `uv sync --python 3.12` 安裝後在 repo 根目錄執行。
預設列計畫；只有 `--fetch` 下載。原檔與 receipt 留在原來源目錄，離線重建需保留兩者。

```bash
uv run -m scripts.official_cross_city ntpc --kind routes --fetch
uv run -m scripts.official_cross_city ntpc --kind stops --fetch
uv run -m scripts.official_cross_city ntpc --kind leapfrog --fetch
uv run -m scripts.official_cross_city ntpc-pathdetail --fetch
uv run -m scripts.official_cross_city population --month 2026-09 --fetch

# 依原始月檔精確進出站名稱選擇，不猜測站名異名。
uv run -m scripts.official_cross_city metro-od \
  --month 2026-09 --corridor-id xinzhuang-guandu \
  --origins '新莊' --destinations '關渡,士林' --bidirectional --fetch

# 指定日期主檔與里界版本對照；個別模組也支援 --offline。
uv run -m scripts.official_cross_city.regional_coverage --date 2026-10-23 --fetch
uv run -m scripts.official_cross_city.population_boundary --month 2026-09 --fetch
uv run -m unittest discover -s tests/official_cross_city -v
```

TDX 模組從專案 `.env` 或環境讀取 `TDX_CLIENT_ID`、`TDX_CLIENT_SECRET`；不輸出金鑰，
不購買額度或改變帳戶方案。基礎五組 CSV／月檔 collector 不需要 TDX 憑證。
多個 TDX collector 同時執行時須共同遵守帳戶限頻；預設間隔不可當成帳戶剩餘配額。

## 格式與失敗處理

- JSON 使用 `schema_version`、`dataset_id`、`metadata`、`records`。缺值為 null，ID 為字串。
- HTTPS host allowlist、TLS 驗證、大小與時間上限；HTML 錯誤頁、空檔、截斷、重複欄位、
  CSV 未閉合引號、欄數錯誤或衝突鍵均停止，保留上一版正規化資料。
- 共用下載器的 raw receipt 保留 SHA-256；離線重建核對內容、來源 URL、HTTP 狀態、下載時間及大小。
  舊 receipt 沒有摘要時須重新下載，不以現有快取補建可信紀錄；摘要只留在 raw receipt。
- CSV 與 feed 必須雙向逐列符合全部 CSV 欄位；任一 feed 列缺少 CSV 對應就停止 ID 對照產製，重新取得兩端來源。
- 完全相同列才去重並保留來源行號；同鍵內容衝突不任意選一筆。方向、重複經過站和多附線關係保留。
- PathDetail 的 `sequenceNo` 從 0 起；`type`、`typeAttribute` 保留原碼，未驗證時不套用其他碼表。
- 人口核對男＋女、單歲與年齡組加總。跨版里界對照保留兩端名稱與實際版本；未編定村里不補人口。
- 捷運進站／出站字典分開；跨月列保留實際日期。`known_*_sum` 只加總已知值，需同時查看缺值數。

## 驗收與分析限制

每份新快照先標為 `independent_review_status=pending`，直到另一位 Agent 逐筆巡檢來源、
欄位、期間、筆數與加總。品質報告在 [`data/quality/`](../data/quality/)，地圖品質報告在
[`map-data/quality/`](../map-data/quality/)。PR Validation 將程式測試、來源完整性、跨來源 ID、
區域覆蓋及合法步行轉乘分開驗收；未知項目不可當作通過。

班距描述不是完整班表，核定班次不是實際班次，捷運旅次不是公車潛在需求。
相鄰負載差無法唯一識別上下車或 OD；IPF／人口權重推估不可重標為觀測。
出口位置、直線距離及缺乏行人通行權／拓樸的 OSM 圖層不能證明合法步行轉乘。
本次三處示範區已有帶原始節點、通行限制及過街檢查的圖網；18 個端點組合仍未通過完整合法路徑驗收。
發布站序與班表不等於指定日期的實際行駛；未取得班表、來源無資料及身分差異各自保留為未知。
路線名稱或區域缺資料不能解讀成沒有服務，不直接據此給出減班或 DRTS 結論。
