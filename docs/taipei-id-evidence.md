# 臺北主管路線 ID 與站序

[`taipei_id_evidence/`](../data/official_cross_city/taipei_id_evidence/)核對完整 TDX Taipei StopOfRoute 與
官方 `blobbus/GetRoute`、`GetStop`、`GetPathDetail`、`GetStopLocation`，不以名稱或鄰近點推測 ID。

| 產物 | 粒度與用途 |
|---|---|
| `memberships.json.gz` | 每個 TDX 站牌會員的主線、附線、業者、方向、StopID／StopUID、StationID 與直接來源 ID；兩端原座標與上下客碼分開保留 |
| `sequences.json.gz` | 每組附線／方向／業者的完整有序 StopID 陣列、原序號及比較結果 |
| `unlisted_direct_stops.json` | 未出現在完整 TDX 會員中的直接來源站牌；缺會員不代表停駛 |
| `summary.json` | 實際筆數、涵蓋 RouteUID 與來源差異分類；不是觀測班次 |

ID 依[TDX 基本公車 v2 規格](https://tdx.transportdata.tw/api-service/swagger/basic/2998e851-81d0-40f5-b26d-77e2f5ac4118)
與[臺北 v6.3 規格](https://pto.gov.taipei/News_Content.aspx?n=A1DF07A86105B6BB&s=55E8ADD164E4F579)核對。
`type` 不是方向；方向只用 StopID 明示連到 GetStop.goBack。序號不重編、不套固定偏移，重複經站不去重。
StationUID 未由此端點發布，保持 `null`；不以主管機關簡碼自行補造。

座標差異不影響已核實的行政 ID 身分，但不代表兩個來源位置可互換；距離僅為差異診斷。
上下客缺值不補 0，兩來源時間各自保留，不推論實際營運或通行權。只有來源明示「虛擬站不停靠」的點
可另列省略；其他序列差異維持待查。使用前查看獨立品質報告及 `source_review_flags`。

```sh
# 先取得完整 Taipei StopOfRoute；此處不發送 TDX 請求。
uv run python -m scripts.official_cross_city.taipei_id_evidence --fetch-direct
uv run python -m scripts.official_cross_city.taipei_id_evidence --offline
uv run python -m unittest discover -s tests/official_cross_city -p test_taipei_id_evidence.py
```

原始檔與收據留 `raw_data/official_cross_city/taipei_id_evidence/`，大型原文不進 Git。
