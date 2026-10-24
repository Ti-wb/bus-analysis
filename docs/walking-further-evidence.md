# 步行缺口補充證據

[`further_evidence.json`](../map-data/walking/further_evidence.json) 逐筆說明士林候選路徑的五個待核邊，以及關渡出口1至站位70056的來源限制。
原圖、網頁、號誌資料及收據保存在 `map-data/raw/walking/further_review/`，不進 Git。

士林最後一邊的車道交會位於實走片段之外；其餘四邊仍涉及兩處未核穿越。
官方站圖和已取得的交維圖不足以證明這兩處當期合法過街，號誌近鄰也不能取代行穿設施證據。
關渡官方圖面可核對出口位置，但沒有提供接至70056的明示站外人行線形。

本檔沒有新增可用轉乘邊，距離保持 `null`；[獨立巡檢](../map-data/quality/walking_further_evidence_review.json)核對原始圖、號誌列與實走片段。
圖面保留原印版本；2026年4至7月的Y26公告與2021至2022年排水施工公告不能延用為當期開放或封閉證明。

[`additional_evidence.json`](../map-data/walking/additional_evidence.json) 另保留新莊工程總量、2026年獎項、截至2026年10月1日的機車禁停範圍及官方目的地交通指引。
地址或路段只作背景，不連接站牌ID；禁停、工程或獲獎資料不代表特定穿越及出口至站牌的接線已核實，指引的約2分鐘也不作案例轉乘成本。
下載連結與收據見 `raw_data/walking_additional/manifest.json`；HTML、PDF和圖片原文留本機，不進Git。
先依來源目錄下載原始檔至各筆 `raw_file`，即可離線重建及巡檢：

```sh
.venv/bin/python -m scripts.official_cross_city.walking_additional --offline
.venv/bin/python scripts/review_walking_additional_evidence.py
```
