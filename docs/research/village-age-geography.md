# 全國清冊與站圈年齡的選擇

2026-10-24 查核。直接沿用專案的 Shapely／pyproj，不新增地理套件。

| 來源／組件 | 實讀證據 | 採用方式 |
| --- | --- | --- |
| 戶政司獨立村里清冊 | [官方代碼頁](https://www.ris.gov.tw/documents/html/5/1/167.html)、11509VillageUTF8.txt：無標題三欄（村里碼、父鄉鎮市區碼、原名）；7,781 列 | 與人口逐碼、父碼及原名雙向核對，不以相同筆數代替集合比較 |
| 國土測繪中心全國里界 | [7438](https://data.gov.tw/dataset/7438)：ZIP 內 `VILLAGE_NLSC_1150817` 及 `Village_Sanhe`，皆 EPSG:3824；主層 7,986、附層 1 筆 | 所有 SHP 層都明確處理，原檔留本機；全國核對表及北北基完整幾何另外輸出 |
| 既有名字證據 | `boundary_evidence.verify_alias`、六筆 `name_aliases.json` 的完整碼／父區／兩端原名 | 僅採用這六個已查證組合，其餘 17 個差異不自動改字或通過 |
| Shapely 2.2.0 | 已讀安裝版 `set_operations.intersection`、`measurement.area`；[intersection 文件](https://shapely.readthedocs.io/en/2.2.0/reference/shapely.intersection.html)、[同一平面限制](https://shapely.readthedocs.io/en/2.2.0/manual.html#coordinate-systems)、[BSD 授權](https://github.com/shapely/shapely/blob/2.2.0/LICENSE.txt) | 直接使用既有套件；北北基投影 EPSG:3826 後求交集及面積，不複製上游程式碼 |
| 既有站圈程式 | `build_web_data.build_catchments` 使用統計區面積分攤 | 新增獨立村里年齡輸出，保留舊統計區來源／期間與路線編輯器，不混為同一人口基準 |

清冊及官方圖資採政府資料開放授權第 1 版，分別標示內政部戶政司、內政部國土測繪中心。

主層檔名可證明版本為 2026-08-17；Sanhe 沒有可證明幾何日期的欄位或檔名，
其版本維持 null。ZIP member timestamp 不是幾何日期。Sanhe 與主層六村重疊，
行政身分有對應也不能視為互斥分攤區；交疊及未審名稱必須揭露、排除分攤。
人口與清冊為 2026-09，不宣稱已取得同月份歷史幾何。

站圈採村內人口均勻分布的面積比例假設。已知部分、完整估算、無邊界、缺人口、
名稱未核實及幾何交疊各自保留；覆蓋率以幾何聯集計，不把缺值補零，海邊未覆蓋不推定無人口。
全國清冊及幾何核對保留遠島，地圖與站圈只涵蓋現有北北基地圖範圍，
不把北北基適用的投影默默套到遠島。這些人口估算不代表真實乘車需求。
