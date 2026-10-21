# 資料更新

在 repo 根目錄執行，需要 [uv](https://docs.astral.sh/uv/) 及 `curl`。Python 版本由 `.python-version` 指定，依賴鎖定在 `uv.lock`。第一次使用 uv 請先看 [uv 快速上手](uv.md)：

```bash
uv sync
```

## 臺北市公車與公運處統計

```bash
# 下載並整理
uv run scripts/update_data.py

# 不連網，從原始檔重新整理
uv run scripts/update_data.py --offline

# 必要檢查
uv run python -m unittest discover -s tests -p test_update_data.py -v
```

原始檔存於 `raw_data/taipei/`、`raw_data/pto/`，來源清單為 `raw_data/taipei_bus_manifest.json`；輸出清單見 [`data/taipei_bus_catalog.json`](../data/taipei_bus_catalog.json)。下載或解析失敗時保留上一版，更新不會覆寫其他來源。

即時到站資料只保留最新快照；每日更新不足以分析歷史班距。PDF 通用抽取的欄位語意仍需核對，限制記錄在各檔案的 `metadata`。

## 自動更新

[`update-taipei-bus-data.yml`](../.github/workflows/update-taipei-bus-data.yml) 在 GitHub 預設分支且 Actions 啟用後，每日臺灣時間 04:17 執行，也可在 Actions 手動觸發。

結果存為 `taipei-bus-data-<run_id>` artifact，保留 30 天，不自動改寫 Git 分支。本機資料更新使用上方指令；Agent 完成資料階段後的 commit／push 依 [AGENTS.md](../AGENTS.md) 執行。
