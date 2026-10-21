# Agent 工作規則

- 原始下載檔與中間產物放 `raw_data/<來源>/`；整理後的 JSON／GeoJSON 才放 `data/`。不得把未解析原文包成 JSON 當作整理完成。
- 每個來源使用獨立腳本、檔名與 manifest，只更新自己負責的檔案；不得清空共用資料夾或覆寫其他 Agent 的工作。`scripts/update_data.py` 負責 `raw_data/taipei/`、`raw_data/pto/`、`raw_data/taipei_bus_manifest.json` 及 README 所列公車輸出。
- JSON 使用 `schema_version`、`dataset_id`、`metadata`、`records`；GeoJSON 使用 `FeatureCollection` 並附 `metadata`。欄位採 `snake_case`，ID 使用字串，數值維持 number，缺值為 `null`，不可補成 0。
- 保留來源頁、下載 URL、原始檔路徑、SHA-256、下載時間、來源時間／統計期間、單位與資料粒度。無來源時間填 `null`；下載時間不代表資料更新時間。
- GeoJSON 統一 WGS84（EPSG:4326）、`[經度, 緯度]`；先確認來源座標系統，有推定或衝突須註明。不可未驗證就連接不同來源 ID。
- PDF 的頁碼、表格及數值列可存為結構化抽取；未核對欄位語意時標記 `layout_extracted_requires_review`。原始文字留在 `raw_data/`。分清班表、班次、段次、到站預估、總計列與修訂版。
- 文件保持最小必要，只描述用途、規則與用法，不記錄技術流水帳。腳本用法寫在 `docs/` 獨立文件，README 只放摘要與連結。
- 完成一個可使用的資料階段後，做最小必要檢查，**主動 commit 並 push**，不需再次確認。使用 Conventional Commits，例如 `feat(data): add Taipei bus datasets`、`fix(data): correct stop coordinates`、`chore(data): refresh bus datasets`。
- 提交前檢查變更與暫存區，只提交自己完成的檔案；保留其他 Agent 未完成或已暫存的內容。使用一般 push，不 force push。
