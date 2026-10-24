# TDX 公車站間旅行時間

`data/tdx_bus_travel_time/route_catalog.json` 是最近取得的完整日期版本臺北路線取用目錄；`observations/` 保留有界路線的完整、未分頁站間時間回應。`coverage.json` 逐一列出已取得、空回應及失敗的查詢；`delivery.json` 列出有界計畫、實際交付與尚未請求清單。跨市路線保留全段，新北、基隆與公路客運的代表查詢不等於全區普查。資料依 [TDX資料使用條款](https://tdx.transportdata.tw/term)使用，各整理檔附條款連結。

官方[站間旅行時間規格](https://tdx.transportdata.tw/api-service/swagger/premium/b22c71d8-5af0-4de2-ab0a-be28983fa051)提供 `City/RouteName` 與 `InterCity/RouteName`，沒有日期或版本參數。因此近三年需求明列為未由此端點提供歷史版本；不能把當期資料複製成三年資料。臺北路線目錄的版本日、官方版本起訖與來源連結另存於 metadata。

資料每筆為路線／子路線／方向，內含站間序號、起訖站牌 ID、平假日標籤與時間區間。`run_time` 單位為秒；服務日標籤 `0` 是週末、`1` 是平日；`24:00` 原樣保留。官方有效期、更新時間與「使用近三個月資料推估」說明均保留。這是歷史推估值，不能解讀為當日實際耗時；精確原始樣本期間未由來源給定。

首站或其他列缺少起站或時間時保留 `null`，不補零。臺北、新北已核對的 ID／站序另見 [臺北 ID 證據](taipei-id-evidence.md)及[新北補證](id-evidence.md)；本旅時檔不另推定未核實的跨來源關係。路線名稱查詢回傳的子路線全部保留，只有同一 TDX 路線 UID 用於取用目錄的涵蓋核對。沒有 UTC offset 的來源時間不擅加時區。

原始回應及獨立 manifest 放 `raw_data/tdx_bus_travel_time/`。307 的既有完整單線回應沿用其原下載時間，沒有重標成新下載。獨立巡檢結果放 `data/quality/tdx_bus_travel_time_review.json`。

```sh
# 預設只列出下一批計畫，不讀取憑證、不連線、不寫檔。
uv run scripts/tdx_bus_travel_time.py

# 取得最近完整日路線目錄及有界代表路線。
uv run scripts/tdx_bus_travel_time.py --fetch

# 單線或自行指定的有界範圍；可重複 --route。
uv run scripts/tdx_bus_travel_time.py --fetch --route 'NewTaipei:918'

# 重建已保存資料；全臺北路線可分批續抓，每次仍受預算限制。
uv run scripts/tdx_bus_travel_time.py --offline
uv run scripts/tdx_bus_travel_time.py --all-taipei
uv run scripts/tdx_bus_travel_time.py --fetch --all-taipei

# 全線續取時用無損gzip，保留完整站間粒度與JSON schema。
uv run scripts/tdx_bus_travel_time.py --fetch --all-taipei --compress --min-request-interval 15
```

預設上限為20次業務請求、單回應8MB、整批20MB及間隔26秒；已成功的來源預設跳過，`--refresh` 才重新取得。另取一份未計入業務請求數的公開官方規格。401／403／429會立即停止，不重試或調整帳戶。規格列示10次請求／1點、1MB／10點，程式不查核剩餘點數、不購買額度。使用專案 `.env` 或環境的 `TDX_CLIENT_ID`、`TDX_CLIENT_SECRET`，不寫入輸出。

單一取用者可指定15秒間隔；每分鐘剩餘額度為0或來源提供 `Retry-After` 時延後下一次請求，跨批次也沿用時間限制。失敗範圍不在每批自動重查，需明確 `--retry-failed`。完整收到但未通過解析的回應另留原文診斷，不覆寫有效版本。`--compress` 的新原始檔與整理檔使用 `.gz`；manifest 的 `bytes` 是HTTP內容量，`stored_bytes` 是磁碟壓縮大小。既有JSON可與gzip混合離線重建。

整批剩餘容量不足一份完整回應預算時會先停止，下一個端點保持未請求，下一批可接續。

官方若明示「目前沒有…公車路線資料」，另標 `availability_status=published_no_data_message` 並保留 `upstream_response_message` 與原文；coverage分開統計來源無資料與技術失敗。這只表示該推估端點沒有對應資料，不代表路線停駛。

HTTP 200 回應中的上游程式錯誤仍列技術失敗，完整原文保留於解析診斷檔；HTTP 狀態成功不代表已有可用旅時。

`taipei_endpoint_acquisition` 分開統計參考路線 UID 與不重複的路線名稱端點，列出成功、官方無資料、技術失敗及未請求數。`all_reference_endpoints_attempted` 只表示全部參考端點都曾嘗試；即使為 `true`，仍須查閱模型涵蓋及缺值，不能解讀為所有路線、站間與時段都有旅行時間。
