"""Preserve exact Metro corridor OD cells instead of only marginal station totals."""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from .common import ROOT, check_url, csv_rows, fetch, integer, metadata, validate_month, write_json

PAGE = "https://data.taipei/dataset/detail?id=63f31c7e-7fc3-418b-bd82-b95158755b4d"
HOSTS = {"tcgmetro.blob.core.windows.net"}
REQUIRED = {"日期", "時段", "進站", "出站", "人次"}
MAX_SELECTED_ROWS = 250_000


def source_for_month(reference_path: Path, month: str) -> tuple[str, dict]:
    """Reuse this repository's official monthly-source inventory; never guess a URL."""
    validate_month(month)
    obj = json.loads(reference_path.read_text(encoding="utf-8"))
    if obj.get("dataset_id") != "metro_od_source_references":
        raise ValueError("Expected the existing metro_od_source_references inventory")
    matched = [r for r in obj["records"] if r.get("source", {}).get("period") == month]
    if len(matched) != 1:
        raise ValueError(f"Expected exactly one published source for {month}, got {len(matched)}")
    source = matched[0]["source"]
    if source.get("page_url") != PAGE:
        raise ValueError("Unexpected official source page in monthly inventory")
    url = check_url(source["download_url"], HOSTS)
    parsed = urlsplit(url)
    if not parsed.path.startswith("/stationod/") or not parsed.path.lower().endswith(".csv"):
        raise ValueError("Unexpected Metro OD resource path")
    return url, matched[0]


def normalize(path: Path, month: str, source: dict, origins: set[str], destinations: set[str],
              *, bidirectional=False) -> dict:
    validate_month(month)
    if not origins or not destinations or any(not name for name in origins | destinations):
        raise ValueError("Explicit, nonempty station names are required")
    selected = {(o, d) for o in origins for d in destinations}
    if bidirectional:
        selected |= {(d, o) for o in origins for d in destinations}
    records = []
    seen_groups, origin_names, destination_names, observed_date_hours = set(), set(), set(), set()
    current_group, group_destinations = None, set()
    source_rows = source_known_sum = source_missing = selected_known_sum = selected_missing = 0
    out_of_month = 0
    for line, row in csv_rows(path, REQUIRED):
        try:
            # Accept official YYYY-MM-DD and YYYY/M/D; preserve the actual calendar date.
            y, m, d = map(int, row["日期"].replace("/", "-").split("-"))
            day = date(y, m, d)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid source date at line {line}") from exc
        hour = integer(row["時段"], nullable=False)
        if hour > 23 or not row["進站"] or not row["出站"]:
            raise ValueError(f"Invalid hour or station at line {line}")
        origin, destination = row["進站"], row["出站"]
        group = (day.isoformat(), hour, origin)
        if group != current_group:
            if group in seen_groups:
                raise ValueError(f"Repeated/noncontiguous origin group: {group}; inspect publisher ordering")
            seen_groups.add(group)
            current_group, group_destinations = group, set()
        if destination in group_destinations:
            raise ValueError(f"Duplicate OD cell: {group}, {destination}")
        group_destinations.add(destination)
        origin_names.add(origin)
        destination_names.add(destination)
        observed_date_hours.add((day.isoformat(), hour))
        value = integer(row["人次"])
        source_rows += 1
        source_missing += value is None
        source_known_sum += value if value is not None else 0
        in_month = day.strftime("%Y-%m") == month
        out_of_month += not in_month
        if (origin, destination) not in selected:
            continue
        selected_missing += value is None
        selected_known_sum += value if value is not None else 0
        if len(records) >= MAX_SELECTED_ROWS:
            raise ValueError("Corridor selection exceeds row budget; narrow the station sets")
        records.append({"service_date": day.isoformat(), "hour": hour,
                        "calendar_weekday": day.weekday(), "origin_station_name": origin,
                        "destination_station_name": destination, "passenger_trips": value,
                        "within_source_month": in_month, "source_row_number": line})
    required_origins = origins | destinations if bidirectional else origins
    required_destinations = destinations | origins if bidirectional else destinations
    missing_o, missing_d = required_origins - origin_names, required_destinations - destination_names
    if missing_o or missing_d:
        raise ValueError(f"Station names not verified in source: origins={sorted(missing_o)}, destinations={sorted(missing_d)}")
    if not records:
        raise ValueError("No selected OD cells; missing does not mean zero demand")
    meta = metadata(source, "臺北大眾捷運股份有限公司", "日期×小時×進站×出站", [
        "是已發生的捷運旅次，不是公車需求、跳蛙轉乘意願或未滿足需求。",
        "按精確來源站名篩選；進站、出站字典分別驗證，不自動猜測異名對照。",
        "缺列不補零；來源人次缺值保留 null。known sums 只加總非缺值，不代表完整總量。",
        "保留月檔內跨月日期並標示 within_source_month；不得靜默刪除跨年加班資料。",
        "calendar_weekday 只是曆日星期（0=週一），不是官方平假日營運日分類。",
        "來源應以日期×小時×進站分組連續排列；重複或不連續群組須人工確認後重處理。"])
    meta.update({"source_month": month, "timezone": "Asia/Taipei", "unit": "人次",
                 "selection": {"origins": sorted(origins), "destinations": sorted(destinations),
                               "bidirectional": bidirectional},
                 "observed_date_range": {"start": min(d for d, _ in observed_date_hours),
                                         "end": max(d for d, _ in observed_date_hours)}})
    return {"schema_version": 1, "dataset_id": "metro_corridor_od", "metadata": meta,
            "records": records, "quality": {"source_row_count": source_rows,
            "source_known_passenger_trips_sum": source_known_sum, "source_missing_count_cells": source_missing,
            "source_out_of_month_rows": out_of_month, "source_observed_date_hours": len(observed_date_hours),
            "selected_row_count": len(records),
            "selected_cells_expected_if_dense": len(selected) * len(observed_date_hours),
            "selected_cells_not_observed": len(selected) * len(observed_date_hours) - len(records),
            "selected_known_passenger_trips_sum": selected_known_sum,
            "selected_missing_count_cells": selected_missing,
            "selected_pairs_not_observed": [list(pair) for pair in sorted(selected - {
                (r["origin_station_name"], r["destination_station_name"]) for r in records})]}}


def names(value: str) -> set[str]:
    result = {name.strip() for name in value.split(",")}
    if "" in result:
        raise argparse.ArgumentTypeError("Station names cannot be empty")
    return result


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True, type=validate_month)
    parser.add_argument("--corridor-id", required=True)
    parser.add_argument("--origins", required=True, type=names)
    parser.add_argument("--destinations", required=True, type=names)
    parser.add_argument("--bidirectional", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch", action="store_true")
    mode.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", args.corridor_id):
        parser.error("corridor-id must be a safe lowercase slug")
    ref = args.root / "data/metro/source_references.json"
    url, reference = source_for_month(ref, args.month)
    if not (args.fetch or args.offline):
        print(json.dumps({"month": args.month, "url": url, "status": "plan_only_no_download"}, ensure_ascii=False))
        return
    raw, receipt = fetch(args.root, f"metro_od_{args.month}.csv", url, PAGE, HOSTS,
                         offline=args.offline, max_mb=600)
    result = normalize(raw, args.month, receipt, args.origins, args.destinations, bidirectional=args.bidirectional)
    result["dataset_id"] = f"metro_corridor_od_{args.corridor_id}"
    # Earlier inventory counts are evidence from an earlier download, not ground truth for this fetch.
    result["metadata"]["previous_inventory_comparison"] = {
        "previous_source_row_count": reference.get("source_row_count"),
        "previous_known_trips_sum": reference.get("source_passenger_trips_sum"),
        "same_row_count": reference.get("source_row_count") == result["quality"]["source_row_count"],
        "note": "同網址內容可能更新；差異需另審核，不視為本次來源缺值的替代。"}
    output = args.root / f"data/official_cross_city/metro_od_{args.month}_{args.corridor_id}.json"
    write_json(output, result)
    print(json.dumps({"output": str(output), **result["quality"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
