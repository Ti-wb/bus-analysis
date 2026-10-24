# 公車 ID 補充證據

`data/official_cross_city/id_evidence/` 補充先前未聯項目的官方證據，不改寫原快照。
來源原檔、HTTP receipt 及調查資料保存在 `raw_data/official_cross_city/id_evidence/`，不進 Git。

| 產物 | 本次結果 |
|---|---|
| `leapfrog_list_equivalence.json` | 65 筆跳蛙 CSV 全數核對；62 筆與官方分類及 GetRoute 全部共同欄位一致，3 筆明列衝突 |
| `missing_nid_routes.json` | 10 條缺 NId 的臺北主線，全數由官方 PTX 票價 XML 的主管機關／主線／附線／業者組合核實 |
| `unlinked_stop_status.json` | 114 站牌全數在新 GetStop 仍存在，透過明示站位 NId 連至 47 個官方主線站序頁；當期 ETA 全部標示今日未營運 |
| `ntpc_tdx_stop_memberships.json.gz` | 43,270 筆 TDX 站序會員以明示內碼核實；2 筆座標來源差異保留 |
| `ntpc_tdx_stop_sequences.json.gz` | 1,383 組附線／方向全序列核對，312 個直接 feed 明示虛擬站會員另列 |
| `ntpc_tdx_unlinked_stop_accounting.json` | 原 114 站牌在完整當期 TDX 站序及兩次 PathDetail 均未列，逐筆保留缺會員界線 |

跳蛙等價核對要求主管機關及類型正確、官方跳蛙分類 NId 指向 GetRoute 明示 ID，且路線名、
起訖描述、平日及假日去返程首末班共八個時間全部一致、只有一個主線 ID。
`seqno` 仍為清單序號，不解讀成路線 ID；名稱一致本身不能接受對照。
這是明列版本的完整共同欄位等價，不是來源提供的永久 ID 對照表。

三筆差異保留未聯結：北大社區—捷運頂埔站的來源末班 07:00 與 feed 07:10 不同；
合宜住宅—捷運亞東醫院站的 07:45 與 07:35 不同；林口文化三路線的 feed 終點另含
「圓山轉運站」。不以名稱或放寬字串規則接受這三筆。

10 條缺 NId 主線改用官方 `blobbus/GetBusRouteFareList.gz`：XML 主管機關為 `TPE`，
`RouteID`、`SubRouteID`、`OperatorID` 組合須與臺北直接 feed 及 TDX 同時一致，並核對路線名。
核實 ID 不會補造缺少的 NId，也不證明預備路線當日有營運。

114 站牌的證據分層保留：GetStop 的 `stopLocationId` → GetStopLocation 的 `id/nid` →
eBus 主線頁明示 `UniRouteId` 及 `UniStopId`；不以名稱或相近座標連接。
兩次 PathDetail 都沒有這 114 站牌的附線會員，`subroute_id` 繼續為 null。
官方 GetEstimateTime 對每筆明示 `-4`；[新北 v5.5 規格](https://pto.gov.taipei/News_Content.aspx?n=A1DF07A86105B6BB&s=C668AE404DCD2EFE)
第 4 頁定義為「今日未營運」。這是所列來源更新時點的狀態，不能推成永久裁撤或缺會員原因。
「暫時裁撤」等名稱註記與當期狀態分開保存。

TDX 新北基本站序端點保留三頁原文，頁數為 500／500／383，最後一頁小於請求上限，
共 635 條主線、1,383 組附線／方向、43,270 個站牌會員。
TDX 規格明定 `StopID`、`RouteID`、`SubRouteID` 為原資料內碼，UID 由主管機關簡碼加內碼組成。
對照同時要求主線／附線／業者明示組合，以及站牌內碼、所屬主線、方向、站位 ID、中文名一致。
座標只作另項比對：湖前街口 `198117` 與外石崁 `176547` 的兩來源座標有差異，保留雙方數值。

站序先以 PathDetail 的 `stopId` 連 GetStop 的 `goBack` 分去返程，再按原 `sequenceNo` 排列。
PathDetail 的 `type=0` 指正常點，不是去程；不可拿它推方向。
1,189 組完整 StopID 序列一致；194 組的直接 feed 多出 312 個會員（204 個唯一站牌），
每個都在來源名稱明示「虛擬站不停靠」。逐一記錄省略點後，其餘 StopID 順序全部一致；
不以模糊名稱移除其他點。TDX 自身有 303 組序號不連續，保留原值，不重編或套固定偏移量。

這份完整 TDX 快照也沒有原 114 站牌的任何會員；因此「已核對全量當期發布站序」
和「仍無附線會員證據」同時成立。它們保持 `subroute_id=null`、`operational_cause=null`
及 `requires_source_review=true`，不能以序列查核通過宣稱缺會員原因已知或永久停用。
此階段只新增三份產物，不重寫前述已完成巡檢的來源快照。

執行前須有原 crosswalk 快照與參考資料；預設只列計畫，不下載。

```sh
python -m scripts.official_cross_city.id_evidence --offline
python -m scripts.official_cross_city.id_evidence --fetch
python -m scripts.official_cross_city.id_evidence --stop-sequence
python -m unittest discover -s tests/official_cross_city -p test_id_evidence.py -v
```

`--fetch` 只下載公開的官方 XML／feed 與相關主線頁，不呼叫 TDX 業務 API。
`--stop-sequence` 只讀既有完整 TDX 原文與 receipts，核對 gzip 儲存長度、原始長度及分頁尾頁證據；不發送請求。
新產物均保持 `independent_review_status=pending`，須由另一位 Agent 巡檢後使用。
