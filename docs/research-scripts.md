# 研究腳本說明

這些是 MVP 預研究用的腳本，在 repo 根目錄執行。先執行 `uv sync`（預設會安裝 `research` 依賴群組，也就是 numpy），再用 `uv run` 執行（uv 的基本用法見 [uv 快速上手](uv.md)）。輸出都在 `raw_data/research/`，且已列入 gitignore。

| 腳本 | 功能 | 輸入 → 輸出 |
| --- | --- | --- |
| `probe_blob.py` | 列出各公車 feed 的結構、筆數與範例 | `raw_data/taipei_feeds/*.gz` → 終端機 |
| `quick_stats.py` | 統計路線數、站位數，以及每個站位停靠的路線數 | 同上 → 終端機 |
| `prelim_overlap.py` | 計算站位與捷運的鄰近度，以及各路線的站點重疊度上限 | feeds ＋ `raw_data/research/mrt_exits.csv` → `derived/*.csv` |
| `gen_mock_ridership.py` | 產生 mock 的逐站分時上下車資料，預設用 `data/pto_extended/route_annual.json` 校準 | → `mock/*.csv` |
| `collect_seat_events.py` | 持續收集 BusSeatEvent 的車上人數 | 即時 feed → `seat_events/seat_events_YYYYMMDD.csv` |
| `probe_demand_weights.py` | 驗證運輸需求權重的可行性：捷運站名對 OSM、早晚方向不對稱、250 m 人口對公車供給落差 | `data/`、`map-data/` → `derived/demand_probe_*.json` ＋ 終端機摘要 |
| `inspect_remote_data.py` | 摘要列出 `data/` 下各資料集的 metadata 與欄位 | `data/` → 終端機 |

```bash
uv run scripts/probe_blob.py raw_data/taipei_feeds
```

```bash
uv run scripts/quick_stats.py
```

```bash
uv run scripts/prelim_overlap.py .
```

```bash
uv run scripts/gen_mock_ridership.py .
```

```bash
uv run scripts/collect_seat_events.py raw_data/research/seat_events --interval 20 --duration 0
```

```bash
uv run scripts/inspect_remote_data.py data
```

```bash
uv run scripts/probe_demand_weights.py --cell-m 250
```

- mock 與分析的模型、限制請見 [07](07_Mock資料規格.md)。
- BusSeatEvent 的可行性評估請見 [11](11_時間地點方向需求熱圖設計.md)。
- 運輸需求權重方法與驗證結果請見 [12](12_運輸需求權重方法.md)。
