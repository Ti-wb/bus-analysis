# 步行轉乘證據

用途是逐筆驗收捷運出口到**正確站位**的步行連接。入口為
[`transfers.json`](../data/official_cross_city/walking_transfers/transfers.json)、
[`paths.geojson`](../map-data/walking/paths.geojson)；未取得連接證據的案例保留原因與 `null` 距離。
範圍為新莊、關渡、士林兩出口及直接 feed 明示同站名的站位，不能外推全市。

## 重建

```sh
uv sync
uv run python -m scripts.official_cross_city.walking_evidence --fetch
# 以本機保留的原始來源重建，不發出請求：
uv run python -m scripts.official_cross_city.walking_evidence
```

原始檔與收據放 `map-data/raw/walking/`，不提交 Git。
來源清單、實際快照時間及筆數在 [`source_manifest.json`](../map-data/walking/source_manifest.json)。
官方設施沒有逐筆更新時間，因此保持 `null`；OSM 保留資料庫快照時間及物件版本／更新時間。
官方輪行設施的經緯度順序以官方 Google Maps 顯示程式交叉核對，原始 datum 未明示，
`crs_status` 如實標為推定，不能當成測量級定位精度。

## 驗收規則

- 圖網保留完整 OSM 節點、線形、通行與障礙標籤；只以相同節點 ID 連接。
  幾何相交、鄰近或橋下重疊都不會自動接邊。
- 只接受明示行人線形或 `foot=yes/designated/official`；禁止、私人、未處理的條件限制、
  開放時間與閘門不明通行權均排除。`oneway:foot` 獨立於汽車單行方向。
- 一般車道交會須有可核對的 OSM 過街節點或官方行穿線覆蓋，並核對同層。小巷延伸線例外尚未取得
  完整的附近穿越設施盤點，因此不以一般人行道覆蓋直接套用[道路交通安全規則第 134 條](https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=K0040013&flno=134)
  的例外。靜態圖資不證明當下號誌或臨時施工狀態，使用者仍須遵守現場管制。
- 出口以官方站名、出口號、OSM 出口標籤及 20 公尺內的位置一致性核身；路徑從 OSM 的出口節點起算，
  不把兩個來源的定位差異造為步行線。站牌維持直接 feed 的 `stop_location_id`、地址及方向，
  只列來源明示可上車的路線站牌；缺上下客欄位者不補成可上車。
- 末端接線須完整包含於有效的官方人行道／標線型人行道／行穿線多邊形，且沒有車道或圍欄交會；高架、地下、室內或有遮蓋而層位不明者不能接地面站牌。
  20 公尺只限制搜尋範圍，距離本身不是可接線的證據。不修補來源無效多邊形作為通行證明。
- `mapped_pedestrian_path_verified` 僅指上述有限、靜態來源驗收；不代表無障礙路線、即時施工查核，
  或全程經主管機關現場認證。其他案例不產生轉乘邊。`candidate_path_steps` 若有值，只是實際 OSM 拓樸的候選路徑，
  另列未核實的車道交會邊，`walking_distance_m` 仍為 `null`，不能作為合法步行結果。

## 開源參考與授權

| 參考 | 已核對行為 | 採用方式 |
|---|---|---|
| [OSMnx graph.py](https://github.com/gboeing/osmnx/blob/main/osmnx/graph.py)、[_overpass.py](https://github.com/gboeing/osmnx/blob/main/osmnx/_overpass.py)，[MIT](https://github.com/gboeing/osmnx/blob/main/LICENSE.txt) | walking 預設雙向、可只保留最大連通分量，標籤由設定控制 | 參考原始 OSM 節點建圖方式；本案需逐筆保留未知與行人單行限制，不直接使用預設 walk 結果 |
| [Pyrosm graphs.py](https://github.com/pyrosm/pyrosm/blob/master/pyrosm/graphs.py)、[graph 文件](https://pyrosm.readthedocs.io/en/latest/graphs.html)，[MIT](https://github.com/pyrosm/pyrosm/blob/master/LICENSE) | walking 預設雙向，預設會移除未連通部分 | 此有限範圍已可直接解析 Overpass JSON，不增加 PBF 工具鏈與圖網依賴 |

本腳本使用專案既有 Shapely／pyproj 及標準函式庫最短路徑；沒有複製上述專案程式。
官方設施層依政府資料開放授權條款；OSM 圖網、路徑及衍生轉乘證據依
[ODbL 1.0](https://www.openstreetmap.org/copyright)，須標示 © OpenStreetMap contributors。
