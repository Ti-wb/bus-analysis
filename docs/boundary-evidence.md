# 人口里碼、里名與里界版本證據

[證據目錄](../data/official_cross_city/boundary_evidence/manifest.json)補充 2026-09 人口與 2026-08-17 NLSC 里界的身分、用字及發布版本驗收。此目錄不改寫人口原名、原始里界或其他來源代碼。

## 六筆正式里名對照

內政部國土測繪中心 [111 年度行政區域圖編製及界線維護工作總報告](https://bmap-nlsc.moi.gov.tw/downloadFile.aspx?INSID=20230131172149832)（2022-12-23）PDF 第 94 頁／印刷頁 83 表 3-1 逐碼列明六筆差異；PDF 第 96 頁／印刷頁 85 表 3-2 列明中括號替代字。第 93–95 頁說明造字、標準地名與 GIS 字型限制。

| 官方里碼 | 行政區 | 戶政／人口原名 | NLSC 原名 |
| --- | --- | --- | --- |
| `65000030017` | 中和區 | 瓦󿾨里（中間字 `U+FFFA8`） | 瓦[磘]里 |
| `65000030054` | 中和區 | 灰󿾨里（中間字 `U+FFFA8`） | 灰[磘]里 |
| `65000070007` | 樹林區 | 󿾵寮里（首字 `U+FFFB5`） | [獇]寮里 |
| `65000120027` | 瑞芳區 | 濓新里 | 濂新里 |
| `65000120028` | 瑞芳區 | 濓洞里 | 濂洞里 |
| `65000200004` | 坪林區 | 石𥕢里 | 石[曹]里 |

[name_aliases.json](../data/official_cross_city/boundary_evidence/name_aliases.json)只允許表中完整「里碼、行政區、兩端原名」組合；同名、相近字或其他代碼不可套用。顯示名可使用 `display_name`，原始名稱仍須保留。這是有官方文件支持的行政身分／用字對照，不是全域 Unicode 替換，也不證明同月幾何等價。

## 版本驗收

- [code_version_checks.json](../data/official_cross_city/boundary_evidence/code_version_checks.json)：戶政司官方 11506 與 11509 版全國代碼表及 11509 刪除清單互核。北北基 1,652 個人口里碼、父區碼與原名一致；全國 11 筆刪除均在高雄。月版代碼不包含幾何。
- [boundary_changes.json](../data/official_cross_city/boundary_evidence/boundary_changes.json)：NLSC [115 年 8 月發布公告](https://www.nlsc.gov.tw/NLSC_Content.aspx?n=11987&sms=9680&s=340491)附件明列淡水區坪頂里 `65000100016`、民生里 `65000100019` 於 2026-08-17 村里界釐整。
- [current_distribution_check.json](../data/official_cross_city/boundary_evidence/current_distribution_check.json)：2026-10-24 重新取得官方目前全國下載檔，仍為 `VILLAGE_NLSC_1150817`。與原下載逐里碼比較北北基 1,677 個圖徵，幾何、完整座標與 DBF 屬性均一致。官方圖資商城新聞回傳清單也另列日期與主題；清單沒有完整歷史涵蓋宣告。
- [municipal_source_diagnostics.json](../data/official_cross_city/boundary_evidence/municipal_source_diagnostics.json)：新北官方替代里界頁標示資料量 1,039，但完整 API 及預覽仍為 1,032，另有瓦嗂等用字衝突。未核實其幾何日期，不拿來取代 NLSC 版本，來源自身代碼只作候選診斷。

人口可依已核實行政身分連接明示版本的里界；分析須同時顯示人口月份與幾何版本。**目前發布成果一致不等於地方在兩日期間從未異動**，`same_month_geometry_verified` 保持 `false`；不能用沒有搜尋到公告來證明沒有異動。

## 重建與檢查

```bash
uv run -m scripts.official_cross_city.boundary_evidence --offline
uv run python -m unittest discover -s tests/official_cross_city -p test_boundary_evidence.py -v
```

重建使用 manifest 中保留的官方下載檔與收據；原文、PDF 與中間圖放 `raw_data/official_cross_city/boundary_evidence/`，目前里界 ZIP 放 `map-data/raw/boundary_evidence/`，均不提交 Git。原基準 ZIP 為 `map-data/raw/validation/villages.zip`。來源時間不明處保留 `null`；下載時間僅識別本次取得成果。每次重建由不同整理者核對來源、PDF 頁面、欄位、版本、筆數與必要加總，再更新獨立巡檢狀態。
