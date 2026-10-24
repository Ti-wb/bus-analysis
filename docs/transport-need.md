# 運輸需求供需落差（第一版）

`scripts/build_transport_need.py` 依 [12 運輸需求權重方法](12_運輸需求權重方法.md) 第 6 節，產生臺北市 250 m 網格的需求、供給與落差，輸出 `data/analysis/transport_need_250.json`，前端「需求落差」模式使用。這是研究草案，權重與時間係數都是假設值。

```bash
uv run scripts/update_office_calendar.py      # 先更新辦公日曆（排除國定假日用）
uv run scripts/build_transport_need.py
uv run scripts/build_web_data.py              # 轉成前端用的欄式檔
```

## 輸入

| 面向 | 來源 |
| --- | --- |
| 居住人口 | `data/population` 最小統計區人口（2024-12），依面積比例分到格子 |
| 重要地點 | `map-data/traffic-focus`：學校、市政機關、菜市場、醫院、商場百貨、科技產業園區（依名稱）、已完工社宅（規劃戶數加權） |
| 捷運活動與方向日型 | `data/metro/station_hourly_flows.json`；平日依 `data/calendar` 排除國定假日，週六、週日只取放假日，日總量低於同類日中位數 60% 的日子（颱風）剔除 |
| 公車供給 | `data/taipei_feeds` 站位、站序、班表 |
| 捷運供給 | `map-data/osm` 軌道車站（與前端捷運車站圖層相同） |

## 輸出

每格一筆，含格心、行政區、人口、是否在分析範圍、缺班表標記；`bands` 下為六個時段（平日早峰、離峰、晚峰、晚間，週六、週日 10–16）的數值陣列，欄位順序見 `metadata.band_fields`。公式、參數、站名對應表與排除日期都寫在 `metadata`。

- 分析範圍：人口 ≥ 100 人，或任一時段地點分數 ≥ 0.5 的格子才排名；其餘保留原始值，排名與落差為 `null`。
- 排名在同一時段、分析範圍內計算；`gap = R(需求) − R(供給)`，以 0.5 分四象限。
- `robust_top5_share`：權重各乘 U(0.8, 1.2) 抽樣 1,000 次，於需求排名前半的格子中落差排進前 5% 的比例。
- 缺班表：約 300 m 內有缺班表附線、且同路線沒有其他有班表附線服務該格時標 `partial`，供給為下限。

巡檢結果見 `data/quality/transport_need_review.json`。尚未納入：方向（進城／出城）、路網距離、建物加權人口、新北市資料，見 12 第 8 節。
