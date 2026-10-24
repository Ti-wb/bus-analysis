# 公車跨來源識別碼

本組保留每個來源的命名空間，以官方明示欄位建立版本化對照。路線、附線、站牌、實體站位和
NId 是不同識別碼，不以名稱相似、編號相同或共用站位直接合併。

| 輸出（`data/official_cross_city/`） | 已核實範圍 |
|---|---|
| `ntpc_feed_id_crosswalk.json` | 新北 CSV 1,106 路線列與 GetRoute 全欄比對一致；完全相同列合併後 1,084 筆，保留主線／附線／業者及 NId、TTIA 碼 |
| `ntpc_leapfrog_crosswalk.json` | eBus 官方跳蛙分類的 70 個主路線 NId，明示連至 GetRoute 主線及 95 個不同附線 |
| `ntpc_leapfrog_list_crosswalk.json` | 跳蛙 CSV 的 65 筆逐筆保留；該來源沒有明示路線 ID，全部保持未連結 |
| `ntpc_unlinked_stops_crosswalk.json` | 114 個 CSV／GetStop 一致、但未出現在 PathDetail 的站牌；附線維持 null |
| `tdx_route_crosswalk.json` | 臺北 416＋新北 635 個 TDX 主線；1,041 個透過唯一官方路線圖 NId 及明示 RouteID 核實，10 個保留未連結 |

上述數量是 2026-10-24 下載新北來源的快照。TDX 新北版本適用 2026-10-23，臺北參考版本適用
2026-10-20；臺北直接 feed 是 2026-10-21。各自來源時間與版本保持獨立，不宣稱同時點營運。
產物須另由獨立 Agent 巡檢，結果見 `data/quality/`。

## 關聯依據與限制

新北[官方 API v5.5](https://pto.gov.taipei/News_Content.aspx?n=A1DF07A86105B6BB&s=C668AE404DCD2EFE)
第 8–9 頁明列 GetRoute 的 `Id`、`NId`、`pathAttributeId`、`pathAttributeNId`、`ttiaPathId`、
`roadMapUrl`，以及 GetStop 的 `Id`、`routeId`、`stopLocationId`。CSV 與 feed 先逐列比較全部
CSV 欄位，只有空字串與 JSON null 視為同一缺值；名稱差異或其他欄位衝突即停止。
NId 可能含英文字母，不能轉成數值或只接受數字。

[大臺北公車新北路線目錄](https://ebus.gov.taipei/EBus2/RouteList2?ct=ntpc)的
`Jump Frog Bus` 區塊，連結明示各主線 NId。GetRoute 再明示其數字路線與附線 ID；
不從 NId 的字元位置猜跳蛙分類，也不將臺北的 `genus` 碼表套到新北。
[跳蛙 CSV](https://data.gov.tw/dataset/174364)的 `seqno` 是來源序號，沒有證據可視為
GetRoute 主線 ID。該 65 筆與官方目前 70 條跳蛙主線不是已核實的一對一清單，不能用名稱補接。

TDX `RouteMapImageUrl` 和直接 feed `roadMapUrl` 僅在同指官方
`https://ebus.gov.taipei/MapOverview?nid=...`、NId 兩側皆只有一個主線、且明示 RouteID 一致時
接受對照。保留每個業者與附線，不把分支當主線數。臺北有 10 條來源圖址為 `nid=null`，
不能用這個共用圖址連接，維持未知；也不因此判定沒有服務。這份對照未涵蓋 TDX 站牌或站位。

新北 33,314 個站牌已與 GetStop 逐欄核對；PathDetail 43,582 筆只覆蓋其中 33,200 個。
未連結 114 個中，39 個名稱含「暫時裁撤」，1 個含「施工取消停靠」，其餘 74 個沒有這些註記。
這些是來源名稱的文字線索，未證明缺列原因或永久停駛；不新增附線關聯、不刪站、不補造序號。
GetRoute／GetStop／PathDetail 的 `EssentialInfo.Location` 在新北官方端點仍寫「台北市」，原文保留。

## 更新

使用 Python 3.12+ 標準函式庫；預設只列計畫，不下載。原檔與 receipt 保存在
`raw_data/official_cross_city/crosswalk/`，不進 Git。先有正確官方來源，全部解析成功才更新整理產物。
完整重建不呼叫 TDX；TDX 使用已取得且通過來源與筆數檢查的正規化版本。

```bash
python -m scripts.official_cross_city.crosswalk --fetch \
  --tdx-route data/tdx/observations/Taipei_2026-10-20_Route_top_100000.json \
  --tdx-route data/official_cross_city/regional_routes/NewTaipei.json

python -m scripts.official_cross_city.crosswalk --offline \
  --tdx-route data/tdx/observations/Taipei_2026-10-20_Route_top_100000.json \
  --tdx-route data/official_cross_city/regional_routes/NewTaipei.json

python -m unittest discover -s tests/official_cross_city -p test_crosswalk.py -v
```
