# 臺北市道路速率、流量與路段歷史

`data/tdx_road_traffic/` 收錄臺北市 TDX 道路資料。`historical_catalog.json` 保留近三年與官方供應期間的交集，並列出每次不超過七日的取用網址；目錄起訖不代表逐日完整性。

資料取用及再利用依 [TDX 服務條款](https://tdx.transportdata.tw/term) 及原資料提供機關授權規定；各資料檔與 manifest 的 metadata 均保留條款連結。

`observations/` 將四種資料分開：Section 是發布路段定義，VD 是偵測器定義，Live 是路段平均旅行速率／時間，Live/VD 是偵測器的路段、車道、車種速率及流量。使用者指定的 [臺北市發佈路段歷史資料 v2](https://tdx.transportdata.tw/api/historical/v2/Historical/Road/Traffic/Section/City/Taipei) 屬於第一類，不含路況觀測。

`availability/` 是指定日官方 Count／SizeBytes，不是車輛觀測。`delivery.json` 列實際交付筆數、完整性及來源缺口。全量現況用回應 Count 核對；全量靜態歷史用獨立 Meta Count 核對。動態歷史只保留前 30 筆樣本，不能用來估計整日或三年的表現。原始檔及獨立來源 manifest 在 `raw_data/tdx_road_traffic/`；原始回應僅在本機保留，Git 保存 manifest 的來源連結、期間與下載資訊。

路段 TravelSpeed 單位為 km/h，TravelTime 為秒。VD 官方 OpenAPI 只稱 Speed 為平均速率、Volume 為流量偵測值，未寫單位及積分期間；保留原值並標示未知，不轉成每小時流量。`-99` 等來源異常值保留；缺值為 null。SrcUpdateTime／UpdateTime 在歷史與基礎版規格中的文字描述相反，兩欄原樣保留，以 DataCollectTime 識別動態觀測時間。

跨來源 ID 不直接連接。`reference_diagnostics.json` 只核對現況定義與觀測的機關及明確 ID 覆蓋，不建立版本關聯；即使同為 TDX，仍應核對 AuthorityCode、日期及版本，現況快照的路段定義不能直接作為所有歷史觀測的路段版本。

請求 2026-10-23 的兩項動態歷史樣本，實際觀測時間分別為臺北 10/22 23:59（路段速率）及 23:58（VD）；VD 的 InfoDate 亦為 2026-10-22。保留原日期與 UTC offset，metadata 分開列出 InfoDate／觀測日偏差筆數、ID 及 `requires_source_review`。官方 Dates 參數說明沒有定義分檔採用哪個時間欄位，不自行推定是 UpdateTime 或將日期改為請求日。

更新用法：

```sh
uv run scripts/tdx_road_traffic.py --plan
uv run scripts/tdx_road_traffic.py --fetch --use-cached-catalog
uv run scripts/tdx_road_traffic.py --offline
```

預設歷史日期採四項服務目錄共同的最新完整日；可加 `--date YYYY-MM-DD`。金鑰只從專案 `.env` 或環境的 TDX_CLIENT_ID／TDX_CLIENT_SECRET 讀取，不輸出、不保存。預設最多 10 次資料請求、每次 8 MB、總計 32 MB、間隔 26 秒；不購買額度。公開目錄更新不需金鑰；`--use-cached-catalog --plan` 可預覽而不連網，`--offline` 僅重建自己的已保存來源。可重複加 `--request-id` 只執行計畫中的指定來源。HTTP 401／403／429 立即停止，保留可取得的非敏感限額標頭；不可藉重試增加額度。失敗更新保留上一版成功資料及 `last_failed_attempt`。官方 API 的預設 top 是 30，完整歷史取用需先查 Meta，再明確設定 top。

達到請求或回應容量上限時，仍寫出已完成的 `delivery.json`，並列停止原因與本輪未請求的來源；錯誤回應實際讀取的位元組也計入容量。回應內文讀取失敗而無法計量時停止本批，總已讀量為 null，另列已知位元組數。後續請求及再次執行均遵守已保存的 `Retry-After` 與每分鐘剩餘額度標頭。新回應須通過傳輸、正規化與嚴格 JSON 序列化才替換成功快取；被拒絕的原文只留作失敗證據，不能因離線解析成功而升為有效資料。

API 參數及欄位定義見 `api_catalog.json`、`field_dictionary.json`；來源位置與下載資訊見 manifest。
