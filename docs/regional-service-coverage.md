# 區域公車站序與服務日曆

`data/official_cross_city/service_coverage/` 保留基隆、公路客運與指定雙北範圍的官方站序或班表；`coverage.json` 列出完整分頁、尚缺頁面、空回應及來源失敗。各 `.json.gz` 解壓後皆為相同 JSON schema，完整保留每個停靠站、班次、班距、`ServiceDay` 與 `SpecialDays`。

資料來自 [TDX 公車v2](https://tdx.transportdata.tw/api-service/swagger/basic/2998e851-81d0-40f5-b26d-77e2f5ac4118)，使用官方 `$top`／`$skip` 依路線、附線、方向排序取至尾頁。沒有資料的端點保留空回應，不解讀為該區沒有營運。現行資料與既有日期版本路線主檔可能不同版；路線UID缺口另列，不用名稱裁切全國公路客運。

班表中的服務日曆、特殊營運日與首末班時間是公開服務資訊，不能證明某一班實際行駛。`min_headway_mins`／`max_headway_mins` 單位為分鐘，時間字串原樣保留，缺值不補零。站牌座標在官方 `PointType` 定義為 WGS84 經緯度；JSON 保留具名經緯度欄位。TDX ID 不直接對接地方 feed。

同一路線、附線、方向可依業者或業者關係分成多個模型，全部保留；不能只依這三碼去重，也不能直接加總各業者班表當作路線班次。

```sh
# 預設只顯示基隆與公路客運計畫。
uv run python -m scripts.official_cross_city.service_coverage

# 可續傳完整分頁，已保存的成功頁預設重用。
uv run python -m scripts.official_cross_city.service_coverage --fetch
uv run python -m scripts.official_cross_city.service_coverage --fetch --scope NewTaipei --kind StopOfRoute
uv run python -m scripts.official_cross_city.service_coverage --fetch --scope Taipei --kind StopOfRoute
uv run python -m scripts.official_cross_city.service_coverage --fetch --scope Taipei --scope NewTaipei --kind Schedule
uv run python -m scripts.official_cross_city.service_coverage --offline
```

每批最多20次、20MB、單回應8MB，預設間隔15秒；剩餘每分鐘額度為0或有 `Retry-After` 時等待。401／403／429立即停止，不自動重試或購買額度。與旅時腳本串行使用，避免多批共同超過帳戶限頻。原始gzip、HTTP位元組數、實際保存位元組數與來源時間存於 `raw_data/official_cross_city/service_coverage/manifest.json`。資料依 [TDX資料使用條款](https://tdx.transportdata.tw/term)使用；獨立巡檢另存 `data/quality/`。
