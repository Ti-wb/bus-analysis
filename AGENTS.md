# Agent 工作規則

- 原始下載檔與中間產物放 `raw_data/<來源>/`；整理後的 JSON／GeoJSON 才放 `data/`。不得把未解析原文包成 JSON 當作整理完成。
- GIS 底圖與行政區界依使用者指定放 `map-data/`；原始下載及中間產物放 `map-data/raw/` 並排除 Git，只提交雙北範圍的整理圖層、來源目錄與使用說明。為完成跨市人口及里界驗收，另允許提交基隆市官方里界與其來源、版本及獨立巡檢；OSM 底圖範圍仍限雙北。
- 每個來源使用獨立腳本、檔名與 manifest，只更新自己負責的檔案；不得清空共用資料夾或覆寫其他 Agent 的工作。`scripts/update_data.py` 負責 `raw_data/taipei/`、`raw_data/pto/`、`raw_data/taipei_bus_manifest.json` 及 README 所列公車輸出。
- 每份資料完成後，由不同於整理者的獨立 Agent 巡檢來源、欄位、期間、筆數及必要加總；發現問題修正後再確認，巡檢結果放 `data/quality/`，地圖資料放 `map-data/quality/`。
- JSON 使用 `schema_version`、`dataset_id`、`metadata`、`records`；GeoJSON 使用 `FeatureCollection` 並附 `metadata`。欄位採 `snake_case`，ID 使用字串，數值維持 number，缺值為 `null`，不可補成 0。
- 保留來源頁、下載 URL、來源時間／統計期間、單位與資料粒度；原始檔有保留時記錄路徑。無來源時間填 `null`；下載時間不代表資料更新時間。不保存雜湊等非必要資訊。
- 資料以臺北市近三年為主；捷運先整理最近一年各站分時進出量。大型原始檔完成整理及巡檢後可只留來源連結，不放進 Git；不要為了完整收錄而讓 repo 過大。靜態圖資保留實際版本，跨市路線或捷運旅次不以未核實的名稱規則裁切。
- GeoJSON 統一 WGS84（EPSG:4326）、`[經度, 緯度]`；先確認來源座標系統，有推定或衝突須註明。不可未驗證就連接不同來源 ID。
- PDF 的頁碼、表格及數值列可存為結構化抽取；未核對欄位語意時標記 `layout_extracted_requires_review`。原始文字留在 `raw_data/`。分清班表、班次、段次、到站預估、總計列與修訂版。
- 文件保持最小必要，只描述用途、規則與用法，不記錄技術流水帳。腳本用法寫在 `docs/` 獨立文件，README 只放摘要與連結。
- 完成一個可使用的資料階段後，做最小必要檢查，**主動 commit 並 push**，不需再次確認。使用 Conventional Commits，例如 `feat(data): add Taipei bus datasets`、`fix(data): correct stop coordinates`、`chore(data): refresh bus datasets`。
- 提交前檢查變更與暫存區，只提交自己完成的檔案；保留其他 Agent 未完成或已暫存的內容。使用一般 push，不 force push。
