# TDX 資料

以臺北市為範圍。近三年歷史資料保留完整取用目錄，並保存最近完整日的少量實際資料；樣本不能用來估計整日或三年的營運表現。

- `data/tdx/reference_catalog.json`：歷史可取得期間與月份、416條路線的加值資料取用網址，以及12區可及性來源。
- `data/tdx/observations/`：已取得並整理的實際資料。每檔 metadata 標明期間、粒度、單位和完整性；檔名含 sample 的資料只是前30筆。
- `data/tdx/availability/`：A1/A2 官方筆數與大小資訊，不是車輛事件。
- `data/tdx/api_catalog.json`、`field_dictionary.json`：官方規格及欄位定義。
- `raw_data/tdx/manifest.json`：來源、下載時間與原始檔；`delivery.json` 列出實際交付和缺口。

臺北2026-10-20單日 A1 為6,648,792筆／3,176,636,931 bytes，A2為1,921,329筆／1,129,991,416 bytes，因此不存全量歷史明細。DRTS有12條路線及11筆預約規則；歷史路線主檔416筆已用來源Count核對。

307站間旅行時間的有效期為2026-10-01至2026-10-31；5,500個站間時段列中86列缺少行駛時間，保留null。307站站班表超過8MB門檻，留取用網址。站間距離的官方端點回傳Request Rejected HTML，保留來源限制，沒有以推算值替代。

臺北12區的公車未涵蓋／公共運輸不可及24份回應已取得。松山不可及資料的原始Point超出松山參考邊界，已加註限制，不可直接作區內分布圖；其餘11區只通過參考邊界bbox篩檢，未聲稱完成逐點行政界驗證。合理家戶數量未被定義為可及戶數，不可用它與家戶數的差值推估不可及戶數。

歷史站序在2026-10-20與2026-10-19均回HTTP500，已留取用網址及來源缺口。

更新公開目錄：`.venv/bin/python scripts/update_tdx.py`；離線重建：`.venv/bin/python scripts/update_tdx.py --offline`。

用 `--offline --plan --family drts` 預覽請求；歷史取樣用 `--offline --plan --family historical --date YYYY-MM-DD --sample-top 30`。執行時改用 `--use-cached-specs --fetch` 並移除 `--offline`。金鑰從專案`.env`或環境的`TDX_CLIENT_ID`、`TDX_CLIENT_SECRET`讀取，不寫入資料檔。

`--max-requests`、`--max-response-mb`、`--max-total-mb` 預設為20次、單回應8MB、每批20MB；目前來源標頭限制每分鐘5次，程式預設間隔13秒。本機上限不等於帳戶剩餘點數；程式不購買額度或升級方案。來源無時區offset的時間原樣保留，不擅加臺灣時區。
