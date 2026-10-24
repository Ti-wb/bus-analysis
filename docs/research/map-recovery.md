# 地圖重試與站點資訊卡

2026-10-24 核對；沿用 React、react-map-gl 8.1.3 與 MapLibre GL JS 6.15.0，不新增依賴。

| 上游 | 實際機制與採用方式 | 授權 |
| --- | --- | --- |
| [react-map-gl](https://github.com/visgl/react-map-gl/blob/3df834290bdecf4f5c9b90d7c117d95af12c1f0a/modules/react-maplibre/src/components/map.tsx) | `_Map` 透過 Promise 建立實例，再更新 imperative ref；卸載會 destroy。直接使用現有依賴，資訊卡須等新地圖 ready 才重新綁定 move 與尺寸監聽。 | MIT，已核對安裝套件 LICENSE |
| [MapLibre GL JS](https://github.com/maplibre/maplibre-gl-js/blob/638b3f9ecab4b743fd4d086d3bfd19bce7b7b032/src/ui/map.ts) | 安裝版 `_contextLost` 會銷毀 style 並發出 webglcontextlost；保留現有重試流程。 | BSD-3-Clause，已核對安裝套件 metadata |

查詢涵蓋 react-map-gl 建立／銷毀實例、MapLibre WebGL 中斷及既有站點定位 hook。只參考介面與生命週期，不複製上游程式碼。僅以 retry 次數重掛資訊卡，仍可能早於非同步 ref 建立；改以地圖 readiness 解除舊監聽並重綁，保留卡片展開與選取狀態。失敗提示須顯示在資訊卡上方，使保留選取時仍可點擊重試。

驗證使用現有瀏覽器測試頁：選取捷運站、模擬 WebGL 中斷、重試後平移，核對站點卡與標記位置，以及卡片展開狀態；另測本地簡圖切換。正式建置與現有前端回歸測試須通過。
