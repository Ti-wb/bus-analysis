<picture>
  <source media="(prefers-color-scheme: dark)" srcset="branding/assets/png/logo-horizontal-reverse@2x.png">
  <img src="branding/assets/png/logo-horizontal-tagline@2x.png" alt="Bus coming soon — Open data bus network GIS platform" width="640">
</picture>

# Bus coming soon｜公車路網需求分析

以開放資料支援公車路網改善的 Web GIS。從臺北市出發，讓市府、客運業者、公車工會與民眾一起回答：**哪裡值得優先改善、路線可以怎麼調整、調整後誰受益或失去服務。**

[開啟地圖](https://bus.tw.pe/) · [啟動與使用](docs/web-map.md) · [完整 User Story 與設計入口](docs/product-stories.md) · [文件索引](docs/README.md)

## 產品主軸

**探索現況 → 排出改善優先順序 → 編輯路線方案 → 比較影響 → 共同討論**

地圖用來理解服務與需求的分布；改善清單把問題整理成可逐項檢視的候選；路線工具讓使用者提出方案，再比較人口、重要設施、班次與路線時長的變化。規劃同時需要顧及高齡者、學生與駕駛工作條件，路線重疊本身不足以判定應該減班或撤線。

## 主要 User Story

| 流程 | 使用者要完成的事 | 目前狀態 |
| --- | --- | --- |
| 探索現況 | 在同一張地圖比較公車、捷運、YouBike 覆蓋與時段供需，點站查看周邊人口及設施。 | 已實作覆蓋、計畫班次、需求落差、村里年齡地圖及站點資訊，含分來源範圍年齡估算，另列所在里統計。 |
| 排定改善優先順序 | 用清單篩選路線或站位／路段，查看問題、指標與明細，再回地圖核對。 | **第 07 頁已實作**，含四類清單、篩選排序、詳情與路線編輯銜接；需求與建議含 Mock Data。 |
| 編輯路線方案 | 新增、編輯或刪除路線，調整站序、路徑與時段班次，預覽後套用並可復原。 | **路線工具已實作**；方案保存在本機瀏覽器。 |
| 比較方案影響 | 分別查看新增／失去服務的人口、班次下降、設施替代服務及單程估時。 | 人口、設施與草圖估時已實作；高齡及學生未估算，人力、車輛、成本與駕駛休息評估待開發。 |
| 共同討論 | 分享相同分析視圖，查閱來源與假設，讓規劃依據可追溯。 | 地圖／清單分享、CSV 匯出與方法說明已實作；路線方案分享待開發。 |

完整故事 ID、完成條件與設計對照見 [User Story](docs/product-stories.md)。此處「已實作」指目前程式提供的功能；設計稿中的互動與示意數字不代表已上線或完成效果驗證。

## 設計與操作入口

- **探索地圖**：[第 04 頁・覆蓋與站點資訊](https://penpot.c3land.org/#/workspace?file-id=94a073f9-47a7-80c1-8008-d636bc5359e3&page-id=7a48f695-9549-80a2-8008-d647e862844e) · [操作與分析方法](docs/web-map.md)
- **改善清單**：[第 07 頁・清單視角](https://penpot.c3land.org/#/workspace?file-id=94a073f9-47a7-80c1-8008-d636bc5359e3&page-id=cd01f655-524b-80aa-8008-d66db7246b95) · [操作與資料判讀](docs/improvement-list.md)
- **路線工具**：[第 04-01 頁・路線編輯與影響評估](https://penpot.c3land.org/#/workspace?file-id=94a073f9-47a7-80c1-8008-d636bc5359e3&page-id=7a48f695-9549-80a2-8008-d66746020a41) · [操作與試算邊界](docs/route-editor.md)

前端已使用 Bus coming soon 品牌；部分 Penpot 畫面仍保留「覆蓋臺北」名稱。品牌素材與規範見 [branding/](branding/) 及[品牌指南](branding/BCS-Brand-Guidelines.pdf)。

## 資料與判讀邊界

資料以臺北市近三年為主，另整理跨市路線、北北基人口里界與旅時等補充來源。**已整理的資料不一定已接入前端**，各來源期間與品質以目錄及 `metadata` 為準。

探索頁已將官方 TDX 的北北基公車站序與班表接入班次網格；新北／跳蛙路網、北北基里人口與年齡、道路速率快照及站間旅時仍可作補充顯示。跨市班次不代表實際運量；原臺北市覆蓋、需求及路線方案人口模型尚未擴充。操作與限制見[前端地圖說明](docs/web-map.md)。

- 覆蓋使用直線距離，未納入實際步行路網與阻隔；計畫班次不等於實際發車或載客量。
- 站點與方案原有人口採 2024-12 戶籍人口、2015 年版統計區界；站卡另有同來源四組年齡分攤。本分支新增 2026-09 村里年齡與 2026-08-17 主里界的地圖／站圈估算，兩套來源、範圍與分組不可混用。人口不是白天活動人口或乘客數，相鄰站圈不可相加。
- 需求落差是研究指標；改善清單結合真實供給與 Mock 需求，優先分數與建議影響不能直接用於裁線或推算營運收益。
- 路線工具是本機假設試算；路徑尚未驗證道路可行性，單程時間依線長、速度及停站假設估計。缺資料保留未知，不補成 0。

[全國村里逐歲人口](docs/moi-village-age.md)與[清冊、里界及站圈](docs/moi-village-age-geography.md)提供可重現抓取、離線重建、全國 7,781 村里核對、北北基 1,677 個圖形及 40,348 站（含跨市原始站碼）的年齡分攤；未核實名稱、交疊與缺值均明列。

[整合資料目錄](data/catalog.json) · [公車基本資料與統計輸出](data/taipei_bus_catalog.json) · [跨市補充資料](data/official_cross_city/validation_catalog.json) · [GIS 圖層](map-data/catalog.json) · [資料巡檢狀態](data/quality/pr_validation.json)

## 開發與維護

前端採 React、TypeScript、MapLibre；資料整理使用 Python 與 uv。指令集中在各自文件：

- [前端啟動、建置與部署](docs/web-map.md) · [路線工具資料與驗證](docs/route-editor.md#產生與驗證)
- [資料整理與來源](docs/data-collection.md) · [資料更新](docs/data-updates.md) · [地圖更新](docs/map-updates.md) · [uv 入門](docs/uv.md)
- [研究文件與方法索引](docs/README.md#研究文件) · [Agent 協作規則](AGENTS.md)

原始檔與中間產物放 `raw_data/`，整理資料放 `data/`，GIS 圖層放 `map-data/`；地圖原始檔放 `map-data/raw/`，不進 Git。保留來源、期間與限制，各來源獨立更新。
