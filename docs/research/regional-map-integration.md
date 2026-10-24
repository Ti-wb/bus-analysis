# 補充資料地圖整合

2026-10-24 查閱；沿用 React、react-map-gl 8.1.3、主線鎖檔的 MapLibre GL JS 6.15.0，不新增地圖套件。

| 上游 | 參考機制 | 授權與判斷 |
| --- | --- | --- |
| [react-map-gl](https://github.com/visgl/react-map-gl/tree/3df834290bdecf4f5c9b90d7c117d95af12c1f0a) | `modules/react-maplibre/src/components/source.ts`：style 載入後建立來源；GeoJSON 變更呼叫 `setData`；來源 ID 固定 | MIT，保留的 Mapbox BSD notices；直接使用既有依賴 |
| [MapLibre GL JS](https://github.com/maplibre/maplibre-gl-js/tree/638b3f9ecab4b743fd4d086d3bfd19bce7b7b032) | `src/source/geojson_source.ts`：GeoJSON 來源、載入錯誤事件；`setData` 回傳 Promise，既有 Source 不依賴回傳值；[官方互動範例](https://maplibre.org/maplibre-gl-js/docs/examples/create-a-hover-effect/)：fill、line 與互動圖層 | BSD-3-Clause；直接使用既有依賴 |

查詢涵蓋 GeoJSON Source／Layer 元件及人口分級著色／滑鼠互動。現有套件均符合需求，無須替換引擎；只參考介面與設計，未複製上游程式碼。

補充資料另產生圖層，依來源明示 ID 與核對結果連接。新北站序連線及道路端點連線標示為示意線；里人口／年齡與站點 350 m 人口估算分開。旅時與速率顯示來源期間，缺值保留 `null`。原臺北市班次、覆蓋與需求分析保持原計算範圍，避免將新顯示圖層誤稱已納入模型。

驗收包括來源與輸出筆數／必要加總的獨立巡檢、缺值及 ID 回歸測試、前端建置、實際瀏覽器開關與點擊，以及補充資料的部署觸發路徑。
