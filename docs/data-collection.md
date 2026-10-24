# 臺北市資料整理

範圍以臺北市近三年為主；月資料從 2023/10 起、年資料保留 2023–2025 完整年。捷運先取 2025/10–2026/09 各站每日分時進出量，完整 OD 與大型原始檔只保留來源連結。靜態圖資保留官方實際版本；臺北相關跨市公車與捷運旅次不以站名猜測行政區裁切。

[完整資料目錄](../data/catalog.json) 列出資料期間、輸出位置、限制與[獨立巡檢結果](../data/quality/)。現行公車優先使用 `data/taipei_feeds/`；原 `data/bus_stops.geojson` 為 2021 年舊圖資。

| 資料 | 整理後目錄 | 更新腳本 |
| --- | --- | --- |
| 公車路網、站序、班表、車輛及即時快照 | `data/taipei_feeds/` | `scripts/update_bus_feeds.py` |
| 路線年報、公司月報、專用道及公車評鑑 | `data/pto_extended/` | `scripts/update_pto_extended.py` |
| 捷運各站分時進出量、出口公車資訊 | `data/metro/` | `scripts/update_metro.py` |
| 里人口年齡、統計區人口與邊界 | `data/population/` | `scripts/update_population.py` |
| 專用道、停車、通行限制、公廁 | `data/urban/` | `scripts/update_urban.py` |
| 全市客運統計、2024 臺北市運具調查 | `data/background/` | `scripts/update_background.py` |
| TDX 歷史及加值資料 | `data/tdx/` | `scripts/update_tdx.py` |
| YouBike 2.0 站點 | `data/youbike/` | `scripts/update_youbike.py` |
| 政府行政機關辦公日曆（2025–2026） | `data/calendar/` | `scripts/update_office_calendar.py` |
| 運輸需求供需落差（研究草案，衍生資料） | `data/analysis/` | `scripts/build_transport_need.py`，見[說明](transport-need.md) |

安裝依賴（`uv sync`）見[既有更新說明](data-updates.md)。各來源獨立更新，例如：

```bash
uv run scripts/update_population.py
uv run scripts/update_population.py --offline
uv run scripts/build_collection_catalog.py
```

各腳本的 `--help` 列出選項。`--offline` 需要仍保留的來源檔；已移除的大型原始檔須重新下載。捷運可從保留的月彙總重組各站分時資料，無法還原完整 OD。

公車欄位及用法見[公車 feeds](taipei-bus-feeds.md)；TDX 金鑰、取樣與額度限制見 [TDX 用法](tdx.md)。`.env` 僅留本機，使用 `.env.default` 的鍵名。

使用資料時請注意：

- 到站預估、計畫班表、車輛實況、行駛班次與行駛段次不同；即時資料只有本次快照。
- 捷運出站人次與戶籍人口不能直接當成公車需求。不同來源 ID 與路線名稱未核實前不可直接關聯。
- 捷運來源的進站與出站名稱不完全相同，兩組字典分開保存，不能用相同向量索引直接比較某站的進出量。
- TDX 歷史事件只留最近完整日的少量樣本，站間時間先留 307 路線；三年歷史與其餘路線保留取用連結，不能把樣本當成完整營運紀錄。
- 全市月報目前至 2026/07；道路旅行時間來源回傳空集合且資料時間停在 2024/12，皆未補零。
- 統計區人口是 2024/12；邊界檔名標示 2015。不同版本保留原值，不能視為最新行政里界。
- 全國公路月報、舊行車成本及業者違規暫緩，僅留[來源連結](../raw_data/background/references.json)。
