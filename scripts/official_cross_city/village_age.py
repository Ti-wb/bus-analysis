"""Rebuild one published MOI 77132 month of Taiwan village age populations."""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
import tempfile
from pathlib import Path

from .common import ROOT, csv_rows, fetch, integer, metadata, monthly_link, text, validate_month, write_json
from .population import period

PAGE = "https://data.gov.tw/dataset/77132"
HOSTS = {"opdadm.moi.gov.tw", "www.ris.gov.tw", "ris.gov.tw"}
NAMESPACE = "moi_village_age"
DATASET_ID = "moi_village_population_age_monthly"
COUNTIES = {
    "09007": "連江縣", "09020": "金門縣", "10002": "宜蘭縣", "10004": "新竹縣",
    "10005": "苗栗縣", "10007": "彰化縣", "10008": "南投縣", "10009": "雲林縣",
    "10010": "嘉義縣", "10013": "屏東縣", "10014": "臺東縣", "10015": "花蓮縣",
    "10016": "澎湖縣", "10017": "基隆市", "10018": "新竹市", "10020": "嘉義市",
    "63000": "臺北市", "64000": "高雄市", "65000": "新北市", "66000": "臺中市",
    "67000": "臺南市", "68000": "桃園市",
}
AGES = [str(age) for age in range(100)] + ["100_plus"]
SEX_FIELDS = {"male": "男", "female": "女"}
AGE_FIELDS = {sex: [f"{age}歲-{label}" for age in range(100)] + [f"100歲以上-{label}"]
              for sex, label in SEX_FIELDS.items()}
BASE_FIELDS = {"統計年月", "區域別代碼", "區域別", "村里", "戶數", "人口數", "人口數-男", "人口數-女"}
REQUIRED = BASE_FIELDS | set(AGE_FIELDS["male"]) | set(AGE_FIELDS["female"])
TRANSPORT_LIMIT = "戶籍人口不是日間活動人口、實際公車上下車人次、通勤 OD 或真實乘車需求；不得以年齡人口直接替代乘車觀測。"


def nullable_sum(values: list[int | None]) -> int | None:
    return None if any(value is None for value in values) else sum(values)


def normalize(path: Path, month: str, source: dict, encoding="utf-8-sig") -> dict:
    """Validate the complete source snapshot; retain exact-age nulls and IDs."""
    validate_month(month)
    if not source.get("downloaded_at"):
        raise ValueError("A source download receipt is required for reproducible metadata")
    records, seen = [], set()
    county_rows = {code: [] for code in COUNTIES}
    missing_cells = 0
    checks = {"sex_population": {"verified": 0, "inconclusive": 0},
              "age_population": {"verified": 0, "inconclusive": 0}}
    for line, row in csv_rows(path, REQUIRED, encoding=encoding):
        if set(row) != REQUIRED:
            raise ValueError(f"Expected the complete 210-column source schema at line {line}")
        if period(row["統計年月"]) != month:
            raise ValueError(f"Source row month differs from requested {month}: line {line}")
        code = row["區域別代碼"]
        if not re.fullmatch(r"[0-9]{11}", code):
            raise ValueError(f"Expected an 11-digit village code at line {line}")
        county = code[:5]
        if county not in COUNTIES:
            raise ValueError(f"Unknown county code {county} at line {line}")
        if not text(row["區域別"]) or not text(row["村里"]):
            raise ValueError(f"Missing administrative/village name at line {line}")
        if not row["區域別"].replace("台", "臺").startswith(COUNTIES[county]):
            raise ValueError(f"Administrative name disagrees with county code at line {line}")
        if code in seen:
            raise ValueError(f"Duplicate village code {code} at line {line}, including conflicting names")
        seen.add(code)
        counts = {field: integer(row[field]) for field in ["戶數", "人口數", "人口數-男", "人口數-女"]}
        ages = {sex: [integer(row[field]) for field in fields] for sex, fields in AGE_FIELDS.items()}
        ages["total"] = [nullable_sum([male, female]) for male, female in zip(ages["male"], ages["female"])]
        populations = {"male": counts["人口數-男"], "female": counts["人口數-女"], "total": counts["人口數"]}
        missing_cells += sum(value is None for value in [*counts.values(), *ages["male"], *ages["female"]])
        if all(value is not None for value in populations.values()):
            if populations["male"] + populations["female"] != populations["total"]:
                raise ValueError(f"Sex/total population mismatch at line {line}")
            checks["sex_population"]["verified"] += 1
        else:
            checks["sex_population"]["inconclusive"] += 1
        for sex in ["male", "female", "total"]:
            age_total = nullable_sum(ages[sex])
            if age_total is not None and populations[sex] is not None:
                if age_total != populations[sex]:
                    raise ValueError(f"Age/{sex} population mismatch at line {line}")
                checks["age_population"]["verified"] += 1
            else:
                checks["age_population"]["inconclusive"] += 1
            record = {
                "period": month, "year": int(month[:4]), "year_roc": int(month[:4]) - 1911,
                "month": int(month[5:]), "area_id": code, "area_level": "village",
                "district_id": code[:8], "county_code": county, "county_name": COUNTIES[county],
                "area_name": row["區域別"], "village_name": row["村里"], "sex": sex,
                "population": populations[sex], "age_counts": ages[sex],
                "source_row": line, "source_file": path.name, "boundary_join_status": "not_verified",
            }
            if sex == "total":
                record["households"] = counts["戶數"]
                county_rows[county].append(record)
            records.append(record)
    missing_counties = [code for code, rows in county_rows.items() if not rows]
    if missing_counties:
        raise ValueError(f"Required counties missing from source snapshot: {missing_counties}")
    county_totals = []
    for code, rows in county_rows.items():
        values = [row["population"] for row in rows]
        county_totals.append({"county_code": code, "county_name": COUNTIES[code], "village_count": len(rows),
                              "population": nullable_sum(values),
                              "population_known_sum": sum(value for value in values if value is not None),
                              "population_unknown_villages": sum(value is None for value in values)})
    totals = [row["population"] for rows in county_rows.values() for row in rows]
    quality = {"source_row_count": len(seen), "village_count": len(seen), "record_count": len(records),
               "source_schema_column_count": len(REQUIRED), "county_count": len(county_totals),
               "unknown_county_count": 0, "missing_source_numeric_cells": missing_cells,
               "population": nullable_sum(totals),
               "population_known_sum": sum(value for value in totals if value is not None),
               "population_unknown_villages": sum(value is None for value in totals),
               "county_totals": county_totals, "reconciliation": checks,
               "coverage_check": "All 22 county prefixes are present in this source snapshot; village completeness is not independently verified."}
    meta = metadata(source, "內政部戶政司", "month × village area_id × sex; 101 age bins per record", [
        TRANSPORT_LIMIT,
        "只驗證來源快照內 22 縣市存在、欄位、唯一鍵與可計算加總，未以獨立全國村里清冊核實村里全量完整性。",
        "行政代碼保留原字串；district_id 是來源 11 碼代碼之前 8 碼階層提示，不表示已驗證與其他里界或公車資料之 ID 關聯。",
        "sex=total 與 male/female 為重疊統計，不可三類加總；人口、邊界及公車觀測期間須分別核對。",
        "缺值保留 null，任一年齡值缺漏之區間加總為 null；100_plus 包含 100 歲以上全部人口。",
    ])
    meta.update({
        "title": "全國村里戶數、單一年齡人口（新增區域代碼）", "source_dataset_id": "77132",
        "period": month, "source_period": {"start": month, "end": month},
        "generated_at": source["downloaded_at"],
        "generated_at_definition": "Fixed snapshot reference from the CSV receipt downloaded_at, not the current conversion time or the source publication/update time.",
        "units": {"population": "persons", "age_counts": "persons", "households": "households"},
        "age_counts_order": list(AGES),
        "age_counts_definition": "Index 0–99 is exact age in years; index 100 is age 100 and older (100_plus).",
        "sex_codes": {"male": "男", "female": "女", "total": "男+女 (derived age bins); 人口數 (published population)"},
        "households_scope": "Published 戶數 appears only in sex=total records and must not be summed across sexes.",
        "source_field_mapping": {"period": "統計年月", "area_id": "區域別代碼", "area_name": "區域別",
                                 "village_name": "村里", "households": "戶數", "population": "人口數",
                                 "male_population": "人口數-男", "female_population": "人口數-女",
                                 "age_counts_male": list(AGE_FIELDS["male"]), "age_counts_female": list(AGE_FIELDS["female"]),
                                 "age_counts_total": "Element-wise male + female; null if either source cell is missing."},
        "source_schema": {"column_count": len(REQUIRED), "required_columns": sorted(REQUIRED), "extra_columns": "rejected"},
        "calendar": "統計年月 accepts ROC YYYMM, Gregorian YYYYMM or YYYY-MM; year = year_roc + 1911.",
        "district_id_definition": "First 8 digits of the unmodified source 區域別代碼; source-code hierarchy only, no external boundary join verified.",
        "county_name_validation": "Source 區域別 must start with its official county name; 台 is normalized to 臺 for validation only, original names are retained.",
        "missing_value_policy": "Source numeric null markers remain null; sums with any missing input are null; known sums and unknown counts are reported separately.",
        "coverage": "Source snapshot with all 22 county prefixes present; not an independent verification of national village census completeness.",
        "transport_interpretation": TRANSPORT_LIMIT,
        "validation": {"unique_key": ["period", "area_id", "sex"], "all_22_counties_present": True,
                       "boundary_join_status": "not_verified", **quality},
    })
    return {"schema_version": 1, "dataset_id": DATASET_ID, "metadata": meta,
            "records": sorted(records, key=lambda record: (record["area_id"], ["male", "female", "total"].index(record["sex"]))),
            "quality": quality}


def write_gzip(path: Path, value: object) -> None:
    """Atomically write compact UTF-8 JSON with a stable gzip header."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
                with io.TextIOWrapper(zipped, encoding="utf-8", newline="\n") as out:
                    json.dump(value, out, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                    out.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def query(path: Path, area_id: str, min_age: int = 0, max_age: int = 100,
          sex: str = "total", month: str | None = None) -> dict:
    """Query one village and one sex; 100 denotes the open-ended 100+ bin."""
    if not re.fullmatch(r"[0-9]{11}", area_id):
        raise ValueError("--area-id must be the exact 11-digit source village code")
    if (isinstance(min_age, bool) or isinstance(max_age, bool) or not isinstance(min_age, int)
            or not isinstance(max_age, int) or not 0 <= min_age <= max_age <= 100):
        raise ValueError("Age range must satisfy 0 <= min-age <= max-age <= 100; 100 means 100+")
    if sex not in {"total", "male", "female"}:
        raise ValueError("sex must be total, male or female")
    if month is not None:
        validate_month(month)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as source:
        obj = json.load(source)
    if (not isinstance(obj, dict) or obj.get("schema_version") != 1
            or obj.get("dataset_id") != DATASET_ID or not isinstance(obj.get("metadata"), dict)
            or not isinstance(obj.get("records"), list)
            or any(not isinstance(row, dict) for row in obj["records"])):
        raise ValueError("Unsupported village age dataset envelope")
    if obj["metadata"].get("age_counts_order") != AGES:
        raise ValueError("Unsupported age_counts_order; expected exact ages 0–99 and 100_plus")
    source_month = validate_month(obj["metadata"]["period"])
    if month is not None and source_month != month:
        raise ValueError("Query month differs from dataset month")
    matches = [row for row in obj["records"] if row.get("area_id") == area_id and row.get("sex") == sex]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one village/sex record; found {len(matches)} for {area_id}/{sex}")
    record = matches[0]
    ages = record.get("age_counts")
    if (record.get("period") != source_month or record.get("area_level") != "village"
            or not isinstance(ages, list) or len(ages) != 101
            or any(value is not None and (type(value) is not int or value < 0) for value in ages)):
        raise ValueError("Invalid village period, level or 101-bin age counts")
    values = ages[min_age:max_age + 1]
    result = {key: record[key] for key in ["period", "area_id", "county_code", "county_name", "area_name", "village_name", "sex"]}
    result.update({"min_age": min_age, "max_age": max_age, "max_age_is_open_ended": max_age == 100,
                   "population": nullable_sum(values), "population_all_ages": record["population"],
                   "missing_age_cells": sum(value is None for value in values)})
    return {"schema_version": 1, "dataset_id": "moi_village_population_age_query",
            "metadata": {"source_dataset_id": DATASET_ID, "source_file": str(path), "period": source_month,
                         "source": obj["metadata"].get("source"), "license": obj["metadata"].get("license"),
                         "units": {"population": "persons"}, "age_range": "Inclusive; age 100 denotes all ages 100 and older.",
                         "sex_selection": "Exactly one sex record; total is never added to male/female.",
                         "transport_interpretation": TRANSPORT_LIMIT}, "records": [result]}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", type=validate_month)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--encoding", choices=["utf-8-sig", "cp950"], default="utf-8-sig")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch", action="store_true")
    mode.add_argument("--offline", action="store_true")
    parser.add_argument("--query", type=Path)
    parser.add_argument("--area-id")
    parser.add_argument("--min-age", type=int, default=0)
    parser.add_argument("--max-age", type=int, default=100)
    parser.add_argument("--sex", choices=["total", "male", "female"], default="total")
    args = parser.parse_args(argv)
    try:
        if args.query is not None:
            if args.fetch or args.offline or args.area_id is None:
                parser.error("--query requires --area-id and cannot be combined with --fetch/--offline")
            print(json.dumps(query(args.query, args.area_id, args.min_age, args.max_age, args.sex, args.month), ensure_ascii=False))
            return
        if args.month is None:
            parser.error("--month is required unless using --query")
        if args.area_id is not None or args.min_age != 0 or args.max_age != 100 or args.sex != "total":
            parser.error("Age/area/sex filters require --query")
        if not (args.fetch or args.offline):
            print(json.dumps({"page_url": PAGE, "month": args.month, "dataset_id": DATASET_ID,
                              "status": "plan_only_select_exact_published_month"}, ensure_ascii=False))
            return
        catalog, catalog_source = fetch(args.root, f"moi_catalog_{args.month}.html", PAGE, PAGE, {"data.gov.tw"},
                                        offline=args.offline, max_mb=5, raw_namespace=NAMESPACE)
        url = monthly_link(catalog.read_text(encoding="utf-8-sig"), PAGE, args.month, HOSTS)
        raw, source = fetch(args.root, f"moi_village_age_{args.month}.csv", url, PAGE, HOSTS,
                            offline=args.offline, raw_namespace=NAMESPACE)
        result = normalize(raw, args.month, source, args.encoding)
        output = args.root / f"data/population/moi_village_age_{args.month}.json.gz"
        write_gzip(output, result)
        raw_dir = args.root / "raw_data" / NAMESPACE
        write_json(raw_dir / "manifest.json", {"schema_version": 1, "dataset_id": "moi_village_age_source_manifest",
                   "metadata": {"provider": "內政部戶政司", "period": args.month,
                                "generated_at": source["downloaded_at"], "license": result["metadata"]["license"]},
                   "records": [{key: value for key, value in item.items() if key != "sha256"}
                               for item in [catalog_source, source]]})
        write_json(raw_dir / "delivery.json", {"group": NAMESPACE, "datasets": [{"dataset_id": DATASET_ID,
                   "status": "normalized", "output_files": [output.relative_to(args.root).as_posix()],
                   "record_count": len(result["records"]), "period": args.month,
                   "limitations": result["metadata"]["limitations"]}]})
        print(json.dumps({"output": str(output), **result["quality"]}, ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
