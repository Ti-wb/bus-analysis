# 地圖資料更新

先依 [資料更新說明](data-updates.md) 安裝 Python 依賴，再執行：

```bash
uv run scripts/update_map_admin.py
uv run scripts/update_map_osm.py --date 2026-10-20
uv run scripts/build_map_catalog.py
```

先更新行政界，再用市界裁切 OSM。`--date` 是 Geofabrik 有供應的日期檔；OSM 原始臺灣下載上限為 350 MiB，只用於擷取雙北。兩個更新腳本加 `--offline` 可從本機 `map-data/raw/` 快取重建；快取不提交 Git。

更新後由獨立 Agent 核對來源、欄位、期間、筆數、幾何及必要關聯，更新 `map-data/quality/`，再執行目錄腳本。目錄會檢查輸出大小與筆數，並比對巡檢紀錄的來源版本，版本不同會標示待巡檢。巡檢結果只適用於已檢查的內容；同版本重新清理或修改腳本也需針對變動重查。

檢查 `map-data/catalog.json` 的巡檢狀態、變更範圍與大小，再提交整理圖層及來源資訊。載入方式、來源授權與資料限制見 [地圖資料說明](../map-data/README.md)。
