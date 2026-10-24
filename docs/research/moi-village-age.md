# 村里逐歲人口的來源與實作選擇

2026-10-24 查核。目標是可重建、可查詢的官方戶籍人口月資料；不估算公車搭乘量。

## 來源與選擇

[內政部資料集 77132](https://data.gov.tw/dataset/77132) 明列 11509 月檔；實際下載為
UTF-8 BOM CSV，210 欄、7,781 村里。全國人口 23,213,251 人，男女及各歲合計吻合。
統計年月為 2026-09；下載時間與資料集詮釋資料更新時間都不是人口統計日期。
授權為[政府資料開放授權條款第 1 版](https://data.gov.tw/license)。

主流程使用官方目錄明列的完整 CSV。官方 ODRP014 API 的英文 schema 與 CSV 不同，
而且單里篩選實測回傳一列時 `totalDataSize` 仍為全國 7,781，不能據此檢查篩選覆蓋。
CSV 可避開分頁漏抓；沒有自動回退 API 或猜測未發布月份的 URL。

研究查詢包含「Taiwan population language:Python」與「csvkit in:name / csv normalize Python dataset」。

| 候選 | 實讀原始碼及授權 | 決定 |
| --- | --- | --- |
| 既有專案 | `official_cross_city.population.normalize` 已清理同份月檔，僅輸出北北基三段年齡；`update_population.normalize_taipei` 已有 `period × area_id × sex`、101 格 `age_counts` | 沿用後者欄位契約，新增獨立來源流程；不改既有里界 crosswalk 輸入 |
| CPython | [csv.py / DictReader](https://github.com/python/cpython/blob/0fb18b02c8ad56299d6a2910be0bab8ad601ef24/Lib/csv.py)、[PSF 授權](https://github.com/python/cpython/blob/0fb18b02c8ad56299d6a2910be0bab8ad601ef24/LICENSE) | 直接用現有標準函式庫；`DictReader` 會補短列、收納多餘欄，因此保留共用 `csv_rows` 的嚴格欄數檢查 |
| csvkit | [cleanup.py / RowChecker](https://github.com/wireservice/csvkit/blob/6d19c625214977ab2988e674441ca24638bc12d3/csvkit/cleanup.py)、[MIT](https://github.com/wireservice/csvkit/blob/6d19c625214977ab2988e674441ca24638bc12d3/COPYING) | 僅參考顯式報列長錯誤的設計，不複製程式碼、不新增依賴 |
| taiwan_population | [README](https://github.com/aszero/taiwan_population/blob/18d0b6be893524a89eb17b33669c260e647a6994/README.md) 指向 MIT，LICENSE 為縮略條款；年度官方 Excel 與視覺化 | 未提供本次逐月村里契約，不採用程式碼 |

## 可驗收邊界

- 共用 HTTPS 下載器、精確月檔選擇、CSV 與非負整數解析；來源獨立放 `raw_data/moi_village_age/`。
- 輸出單一官方月份的全國村里、男女及合計逐歲數。100 歲以上保留開放上界，缺值傳遞為 null。
- 原始代碼含前導零、名稱含罕用字與造字；完整保留，不用名稱猜配或截碼連接其他來源。
- 同一來源 receipt 離線重建的壓縮檔應相同。原檔與 receipt 留在本機；Git 保存整理檔、manifest、delivery 及獨立巡檢。
- 由另一位 Agent 以原始 CSV 逐格核對輸出；測試包含格式、重複碼、月份、缺值、加總、查詢與故障保留。
- 本流程驗證下載月檔內部一致性；沒有獨立全國村里名冊、同月份全國里界、日間活動人口或乘車 OD 的外部驗收。

既有公車／地理介面查核：`build_catchments` 使用最小統計區與面積比例分攤，
`build_transport_need` 使用假設權重和時段係數。新村里資料沒有直接接入兩者；
里碼與最小統計區碼無共同契約，里總人口不能當作站圈人口、乘客人次或缺班數。
