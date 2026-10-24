# 臺北公車站間推估旅時覆蓋

以完整臺北 `StopOfRoute` 為分母，逐附線、方向、原始相鄰站對與服務日標籤核對已保存的 premium 旅時。只認明示 `RouteUID / SubRouteUID / Direction / FromStopID / ToStopID`；premium 規格稱起迄站「站牌代碼」，不是 `StationID`。臺北直接來源內碼層級另見已獨立核對的 [ID 證據](taipei-id-evidence.md)。不依名稱或 `Sequence` 偏移補配。

```sh
uv run python scripts/official_cross_city/travel_time_coverage.py
uv run python scripts/official_cross_city/travel_time_coverage.py --refresh-inventory
```

第一行離線重建固定的 `source_inventory.json`；第二行改用目前已保存的旅時來源母體，均不呼叫 API。取得下一批資料後須明確更新 inventory，再由另一 Agent 巡檢。主旅時資料仍由原 collector 管理。

輸出位於 `data/official_cross_city/travel_time_coverage/`：

| 檔案 | 用途 |
|---|---|
| `source_inventory.json` | 固定取用母體、HTTP receipt、原始檔與整理檔路徑 |
| `reference_models.json.gz` | 完整原序、重複站、迴圈與上下客碼 |
| `model_service_coverage.json.gz` | 每個參考模型 × 服務日，分來源模型保留有效期、原時段、各時段站對支援數 |
| `pair_coverage.json.gz` | 每個相鄰站對出現位置 × 服務日，按來源模型保留匹配列號與可用數值時段列號 |
| `premium_models.json.gz` | 所有回傳模型逐筆列帳，含參考範圍外 UID、空模型與服務日缺列 |
| `unmatched_rows.json.gz` | 首站缺起點、非相鄰、ID 缺列、範圍外或重複站對歧義的原列 |
| `summary.json` | 上述母體及狀態加總 |

`matched` 只表示至少一個原發布時段有明示相鄰站對及可用數值。服務日 `0` 是周末、`1` 是平日，不是國定假日曆。時段與有效期逐來源保留；部分時段不可解讀全日，來源估值不可當成實際行駛或三年歷史。

站對的 `source_rows_by_premium_instance` 以 `premium_instance_id` 連到 `premium_models.json.gz` 的 `source_id`、`source_model_number`，再由來源目錄取得原 observation 檔。原值位於 `records[模型列號-1].travel_times[站間列號-1]`；兩種列號均從 1 開始。`source_row_numbers` 保留來源次序，`usable_numeric_window_row_numbers` 是其中有非負秒數及有效時段的子集。原時段、秒數與 `Sequence` 由此精確取回，避免在每個站對重複大字典。

未取用、來源明示無資料、模型／服務日／站對未觀察及不支援 ID 分開保留。首站缺起點不構成站對；缺秒數維持 `null`，來源明示 `0` 另列。迴圈中重複的相同站對若無法區分出現位置，保留候選位置而不任配。上下客碼原樣保留，有旅時不代表可在該站上下客。不同版本不支援的站對保持待核，不據此判定停駛。

已嘗試但技術失敗的端點，其模型與站對保留 `request_technical_failure` 及原始回應收據；未發布可用模型時不填入旅時。

依 [TDX 資料使用條款](https://tdx.transportdata.tw/term) 使用。每份輸出保留官方規格、來源目錄及獨立巡檢狀態；完成來源巡檢不等於全市、全日或實際營運驗收。
