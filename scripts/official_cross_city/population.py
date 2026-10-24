"""MOI dataset 77132: one explicitly chosen month for Taipei/New Taipei/Keelung."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .common import ROOT, csv_rows, fetch, integer, metadata, monthly_link, validate_month, write_json

PAGE = "https://data.gov.tw/dataset/77132"
HOSTS = {"opdadm.moi.gov.tw", "www.ris.gov.tw", "ris.gov.tw"}
COUNTIES = {"63000": "臺北市", "65000": "新北市", "10017": "基隆市"}
REQUIRED = {"統計年月", "區域別代碼", "區域別", "村里", "戶數", "人口數", "人口數-男", "人口數-女"}


def period(value: str) -> str:
    if re.fullmatch(r"\d{5}", value):
        return validate_month(f"{int(value[:3]) + 1911:04d}-{value[3:]}")
    if re.fullmatch(r"20\d{4}", value):
        return validate_month(f"{value[:4]}-{value[4:]}")
    return validate_month(value)


def normalize(path: Path, month: str, source: dict, encoding="utf-8-sig") -> dict:
    validate_month(month)
    records, seen = [], set()
    selected_counties = set()
    total_rows = missing_cells = 0
    age_columns = {f"{age}歲-{sex}" for age in range(100) for sex in ["男", "女"]} | {"100歲以上-男", "100歲以上-女"}
    # Fail closed if the publisher has changed to a different English schema.
    for line, row in csv_rows(path, REQUIRED | age_columns, encoding=encoding):
        total_rows += 1
        if period(row["統計年月"]) != month:
            raise ValueError(f"Source row month differs from requested {month}: line {line}")
        code = row["區域別代碼"]
        if not code.isdigit():
            raise ValueError(f"Invalid administrative code at line {line}")
        county = code[:5]
        if county not in COUNTIES:
            continue
        if not row["區域別"] or not row["村里"]:
            raise ValueError(f"Missing village/administrative name at line {line}")
        key = (month, code, row["村里"])
        if key in seen:
            raise ValueError(f"Duplicate village record: {key}")
        seen.add(key)
        selected_counties.add(county)
        counts = {name: integer(row[name]) for name in ["戶數", "人口數", "人口數-男", "人口數-女"]}
        males = [integer(row[f"{age}歲-男"]) for age in range(100)] + [integer(row["100歲以上-男"])]
        females = [integer(row[f"{age}歲-女"]) for age in range(100)] + [integer(row["100歲以上-女"])]
        values = [*counts.values(), *males, *females]
        missing_cells += sum(v is None for v in values)
        for sex, ages in [("男", males), ("女", females)]:
            expected = counts[f"人口數-{sex}"]
            if expected is not None and all(v is not None for v in ages) and sum(ages) != expected:
                raise ValueError(f"Age/sex total mismatch at line {line}")
        if all(counts[k] is not None for k in ["人口數", "人口數-男", "人口數-女"]):
            if counts["人口數-男"] + counts["人口數-女"] != counts["人口數"]:
                raise ValueError(f"Sex/total mismatch at line {line}")
        def age_sum(start, stop):
            cells = males[start:stop] + females[start:stop]
            return sum(cells) if all(v is not None for v in cells) else None
        records.append({"period": month, "county_code": county, "county_name": COUNTIES[county],
                        "source_area_code": code, "area_name": row["區域別"], "village_name": row["村里"],
                        "households": counts["戶數"], "population": counts["人口數"],
                        "population_male": counts["人口數-男"], "population_female": counts["人口數-女"],
                        "population_age_0_14": age_sum(0, 15), "population_age_15_64": age_sum(15, 65),
                        "population_age_65_plus": age_sum(65, 101), "source_row_number": line,
                        "boundary_join_status": "not_verified"})
    if selected_counties != set(COUNTIES):
        raise ValueError(f"Required counties missing: {sorted(set(COUNTIES) - selected_counties)}")
    meta = metadata(source, "內政部戶政司", "統計年月×來源區域代碼×村里", [
        "戶籍人口是居住人口參考，不是日間活動人口、公車上下車或通勤 OD。",
        "不將來源行政代碼補零或截短去配另一版里界；boundary_join_status 未驗證。",
        "僅保留北北基各里總數與年齡組彙總；單歲原始值可由官方原檔重建。",
        "年齡組包含缺值時保留 null，不以零補齊。"])
    meta.update({"period": month, "field_units": {"population*": "人", "households": "戶"}})
    return {"schema_version": 1, "dataset_id": "regional_village_population", "metadata": meta,
            "records": sorted(records, key=lambda r: (r["source_area_code"], r["village_name"])),
            "quality": {"source_row_count": total_rows, "record_count": len(records),
                        "selected_counties": sorted(selected_counties), "missing_numeric_cells": missing_cells}}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True, type=validate_month)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--encoding", choices=["utf-8-sig", "cp950"], default="utf-8-sig")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch", action="store_true")
    mode.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    if not (args.fetch or args.offline):
        print(json.dumps({"page_url": PAGE, "month": args.month,
                          "status": "plan_only_select_exact_published_month"}, ensure_ascii=False))
        return
    catalog, _ = fetch(args.root, f"moi_catalog_{args.month}.html", PAGE, PAGE, {"data.gov.tw"},
                       offline=args.offline, max_mb=5)
    url = monthly_link(catalog.read_text(encoding="utf-8-sig"), PAGE, args.month, HOSTS)
    raw, source = fetch(args.root, f"moi_population_{args.month}.csv", url, PAGE, HOSTS, offline=args.offline)
    result = normalize(raw, args.month, source, args.encoding)
    output = args.root / f"data/official_cross_city/population_{args.month}.json"
    write_json(output, result)
    print(json.dumps({"output": str(output), **result["quality"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
