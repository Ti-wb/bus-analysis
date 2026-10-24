# 北北基地理會員與服務發布覆蓋

[彙整目錄](../data/official_cross_city/regional_service_evidence/support.json)列出已取得的各主管機關站序／班表端點。從完整 `StopOfRoute` 保留全國模型，以站點座標判定臺北、新北、基隆會員，再按原始 `RouteUID`、`SubRouteUID`、業者與方向核對班表、`ServiceDay` 及 `SpecialDays` 的發布支持。

| 檔案 | 用途 |
| --- | --- |
| `stop_geography.json.gz` | 每個明示 StopUID、StopID、StationID、原始座標組合的地理結果；保留同 UID 的不同來源座標／站位版本與名稱。 |
| `service_models.json.gz` | 每個原站序模型 × 明示業者關係的跨市會員、原站序與班表／日曆發布狀態。 |
| `schedule_calendars.json.gz` | 原班表模型與每筆 timetable／frequency 的完整 ServiceDay、SpecialDays 物件；完整 StopTimes 仍在來源檔。 |
| `coverage_summary.json` | 按來源主管範圍與停靠城市彙整 RouteUID、附線與業者關係的發布覆蓋。 |
| `support.json` | 每個設定範圍／端點的完整、部分或未取得狀態。 |

JSON 使用來源檔路徑、官方頁面、實際下載 URL、來源 UpdateTime 與下載時間；gzip 解壓後使用相同 JSON envelope。ID 保持字串，數量保持 number，來源缺值為 `null`。

## 地理判定

[TDX 公車 v2](https://tdx.transportdata.tw/api-service/swagger/basic/2998e851-81d0-40f5-b26d-77e2f5ac4118)的 `PointType` 明示 `PositionLon`／`PositionLat` 為 WGS84。`StationID` 是站牌所屬的**站位 ID**；本模型沒有 StationUID，因此保留 `station_uid: null`，不由站位 ID 加前綴補造，也不連接地方 feed。

行政遮罩使用已核對的 NLSC `VILLAGE_NLSC_1150817`，依官方 COUNTYCODE 取北北基全部里與未編定區塊，各市聯集後判定；這是同版里界聯集，不宣稱另版法定市界。來源 PRJ 為 EPSG:3824，轉 EPSG:4326 的 operation 與精度另記於 metadata。原圖形不修補、不以名稱或 bbox 裁切來源模型。

使用 `covers`，來源邊界上的點納入，若同時被多市遮罩覆蓋就保留所有市碼。缺座標、零座標及無效座標分開標示；不把它們補成 0 或判為區域外。只有所有已知點都在區域外且沒有座標缺口，模型才標示 `no_regional_stop_observed`。此狀態只描述停靠點，不證明整條路線軌跡不經過該區。

## 發布狀態

- `published`：在已取得來源中找到完整相同 UID／附線／業者／方向的模型或指定日曆物件。
- `not_observed_in_complete_published_endpoint`：對應端點已取至尾頁，未見指定模型或物件。這不是停駛、無服務或沒有特殊營運日的證明。
- `indeterminate_partial_or_not_requested`：班表端點尚未取得、僅有部分頁面，或業者關係無法核實。
- `indeterminate_missing_schedule_operator`：找到相同範圍／RouteUID／SubRouteUID／方向的班表，但來源未發布 `OperatorID`，無法核實指定業者的五維關係。候選列於 `unassigned_operator_schedule_evidence_ids`，其日曆數另列；不以站序業者補造或複製班表。已有明示業者的精確匹配時只計精確匹配，候選仍分開保留。

不同業者可能共用路線／附線／方向，不按這三碼去重，也不直接合計班表。每個模型可停靠多市，不能把城市摘要相加當成不重複路線數。週別旗標及特殊日碼只保留與檢查來源內容；全 0 的旗標仍保留 0，不推論某一班次是否實際行駛。

```bash
uv run -m scripts.official_cross_city.regional_service_evidence --offline
uv run python -m unittest discover -s tests/official_cross_city -p test_regional_service_evidence.py -v
```

腳本不打 API，也不改來源檔。完整 GIS 環境執行全部測試；僅標準函式庫的 CI 明示跳過 Shapely 邊界案例，其餘座標缺值、發布支持與日曆規則照跑。重建後由不同整理者巡檢來源、CRS、邊界點、完整 UID／業者關係、期間與加總，結果放 `data/quality/`。
