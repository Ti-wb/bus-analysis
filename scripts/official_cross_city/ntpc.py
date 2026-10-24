"""New Taipei complete CSV exports; no name-based cross-provider joins."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import ROOT, csv_rows, fetch, integer, coordinate, metadata, text, write_json

SOURCES = {
    "routes": {"dataset": "123112", "uuid": "0ee4e6bf-cee6-4ec8-8fe1-71f544015127",
               "required": {"id", "providerid", "namezh", "pathattributeid"}},
    "stops": {"dataset": "123091", "uuid": "34b402a8-53d9-483d-9406-24a682c2d6dc",
              "required": {"id", "routeid", "namezh", "seqno", "pgp", "goback", "longitude", "latitude", "stoplocationid"}},
    "leapfrog": {"dataset": "174364", "uuid": "f5e9a430-f337-476f-801e-dc167a112aa2",
                 "required": {"type", "seqno", "agency_codes", "routename", "start_and_end", "weekday_operation_hours", "holiday_operation_hours"}},
}
ROUTE_FIELDS = {
    "id": "route_id", "providerid": "operator_id", "providername": "operator_name",
    "namezh": "route_name", "nameen": "route_name_en",
    "pathattributeid": "subroute_id", "pathattributename": "subroute_name",
    "pathattributeename": "subroute_name_en", "buildperiod": "build_period_raw",
    "departurezh": "departure_name", "departureen": "departure_name_en",
    "destinationzh": "destination_name", "destinationen": "destination_name_en",
    "realsequence": "approved_trip_count_raw", "distance": "round_trip_distance_raw",
    "gofirstbustime": "outbound_first_time_raw", "backfirstbustime": "inbound_first_time_raw",
    "golastbustime": "outbound_last_time_raw", "backlastbustime": "inbound_last_time_raw",
    "peakheadway": "peak_headway_raw", "offpeakheadway": "off_peak_headway_raw",
    "bustimedesc": "weekday_service_description", "headwaydesc": "weekday_headway_description",
    "holidaygofirstbustime": "holiday_outbound_first_time_raw",
    "holidaybackfirstbustime": "holiday_inbound_first_time_raw",
    "holidaygolastbustime": "holiday_outbound_last_time_raw",
    "holidaybacklastbustime": "holiday_inbound_last_time_raw",
    "holidaybustimedesc": "holiday_service_description",
    "holidaypeakheadway": "holiday_peak_headway_raw",
    "holidayoffpeakheadway": "holiday_off_peak_headway_raw",
    "holidayheadwaydesc": "holiday_headway_description",
    "segmentbufferzh": "fare_zone_description", "segmentbufferen": "fare_zone_description_en",
    "ticketpricedescriptionzh": "fare_description", "ticketpricedescriptionen": "fare_description_en",
}
STOP_FIELDS = {
    "id": "stop_id", "routeid": "route_id", "namezh": "stop_name", "nameen": "stop_name_en",
    "pgp": "boarding_alighting_code_raw", "goback": "direction_code_raw",
    "stoplocationid": "stop_location_id", "address": "address",
    "longitude": "longitude_raw", "latitude": "latitude_raw",
    "showlon": "display_longitude_raw", "showlat": "display_latitude_raw", "vector": "bearing_raw",
}


def urls(kind: str) -> tuple[str, str]:
    source = SOURCES[kind]
    return (f"https://data.gov.tw/dataset/{source['dataset']}",
            f"https://data.ntpc.gov.tw/api/datasets/{source['uuid']}/csv/file")


def normalize(path: Path, kind: str, source: dict, encoding="utf-8-sig") -> dict:
    records, by_key = [], {}
    invalid_coordinates = duplicate_rows = source_rows = 0
    direction_codes = set()
    for row_number, row in csv_rows(path, SOURCES[kind]["required"], encoding=encoding, lower_headers=True):
        source_rows += 1
        if kind == "routes":
            record = {new: text(row.get(old)) for old, new in ROUTE_FIELDS.items()}
            key = tuple(record[k] for k in ["route_id", "subroute_id", "operator_id"])
            known = set(ROUTE_FIELDS)
        elif kind == "stops":
            record = {new: text(row.get(old)) for old, new in STOP_FIELDS.items()}
            record["sequence_no"] = integer(row["seqno"], nullable=False)
            xy = coordinate(row["longitude"], row["latitude"])
            record["position"] = {"longitude": xy[0], "latitude": xy[1]} if xy else None
            invalid_coordinates += xy is None
            direction_codes.add(record["direction_code_raw"])
            key = (record["route_id"], record["stop_id"], record["direction_code_raw"], record["sequence_no"])
            known = set(STOP_FIELDS) | {"seqno"}
        else:
            record = {"service_type_raw": text(row["type"]), "source_sequence": text(row["seqno"]),
                      "agency_codes_raw": text(row["agency_codes"]), "route_name": text(row["routename"]),
                      "origin_destination_description": text(row["start_and_end"]),
                      "weekday_operation_hours_raw": text(row["weekday_operation_hours"]),
                      "holiday_operation_hours_raw": text(row["holiday_operation_hours"]),
                      "route_id": None, "subroute_id": None, "linkage_status": "unverified"}
            key = tuple(record[k] for k in ["service_type_raw", "source_sequence", "agency_codes_raw", "route_name"])
            known = SOURCES[kind]["required"]
        if any(v is None or v == "" for v in key):
            raise ValueError(f"Missing source key in {kind} at CSV line {row_number}")
        record["source_namespace"] = "ntpc_open_data"
        record["source_extra_fields"] = {k: text(v) for k, v in row.items() if k not in known}
        # Exact duplicates can be collapsed. Conflicting rows must not silently win.
        if key in by_key:
            existing = records[by_key[key]]
            if record != {k: v for k, v in existing.items() if k != "source_row_numbers"}:
                raise ValueError(f"Conflicting source key in {kind}: {key}")
            existing["source_row_numbers"].append(row_number)
            duplicate_rows += 1
            continue
        by_key[key] = len(records)
        record["source_row_numbers"] = [row_number]
        records.append(record)
    limits = [
        "保留主管機關來源的全部列；不依市界裁斷跨市服務。",
        "不是完整北北基路網；基隆、市區與公路客運仍需補齊。",
        "來源原碼只在 ntpc_open_data 命名空間使用；不可與臺北或 TDX ID 直接等同。",
        "完整下載檔不等於已驗證主管機關的全量營運覆蓋；下載時間不等於來源更新時間。",
    ]
    if kind == "routes":
        limits += ["班距、首末班、核定班次與里程保留來源描述；不是可直接排班的逐班出發時刻。",
                   "核定班次不是實際行駛班次；主線×附線×業者的多列不可直接相加。"]
    elif kind == "stops":
        limits += ["是路線站牌資料，不是實體站位數；未核實前不推論附線完整站序。",
                   "上下車與方向原碼未套用其他機關碼表；未驗證即不生成可搭乘或轉乘邊。",
                   "座標無效保留 position=null 與原始值；不改用顯示座標或猜測座標系統。"]
    else:
        limits += ["跳蛙清單更新頻率較低；不是最新逐班班表、訂票量或核定容量。",
                   "清單沒有可直接驗證的路線 ID；不以名稱猜測關聯。"]
    meta = metadata(source, "新北市政府交通局", {
        "routes": "主管來源主線×附線×業者", "stops": "主管來源路線×站牌×方向×站序",
        "leapfrog": "跳蛙公車清單項目"}[kind], limits)
    meta["field_units"] = {"position.longitude": "degree_east", "position.latitude": "degree_north",
                           "sequence_no": "source_sequence", "*_raw": "來源描述／代碼，未轉換單位"}
    return {"schema_version": 1, "dataset_id": f"ntpc_{kind}", "metadata": meta,
            "records": records, "quality": {"source_row_count": source_rows, "record_count": len(records),
            "exact_duplicate_rows_collapsed": duplicate_rows, "invalid_coordinate_rows": invalid_coordinates,
            "direction_codes_raw": sorted(v for v in direction_codes if v is not None)}}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=[*SOURCES, "all"], default="all")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--encoding", choices=["utf-8-sig", "cp950"], default="utf-8-sig")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch", action="store_true", help="Explicitly download official full CSV files")
    mode.add_argument("--offline", action="store_true", help="Use existing raw files AND matching receipts")
    args = parser.parse_args(argv)
    kinds = list(SOURCES) if args.kind == "all" else [args.kind]
    for kind in kinds:
        page, url = urls(kind)
        if not (args.fetch or args.offline):
            print(json.dumps({"kind": kind, "page_url": page, "download_url": url,
                              "status": "plan_only_no_download"}, ensure_ascii=False))
            continue
        raw, receipt = fetch(args.root, f"ntpc_{kind}.csv", url, page, {"data.ntpc.gov.tw"}, offline=args.offline)
        result = normalize(raw, kind, receipt, args.encoding)
        output = args.root / f"data/official_cross_city/ntpc_{kind}.json"
        write_json(output, result)
        print(json.dumps({"output": str(output), **result["quality"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
